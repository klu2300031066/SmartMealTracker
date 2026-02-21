from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver


class Meal(models.Model):
    CATEGORY_CHOICES = [
        ('breakfast', 'Breakfast'),
        ('lunch', 'Lunch'),
        ('dinner', 'Dinner'),
        ('snacks', 'Snacks'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    calories = models.IntegerField()
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='breakfast',
    )
    date_added = models.DateField(default=timezone.localdate)

    def __str__(self):
        return f"{self.name} ({self.get_category_display()}) — {self.user.username}"


class InventoryItem(models.Model):
    UNIT_CHOICES = [
        ('kg',  'Kilograms (kg)'),
        ('g',   'Grams (g)'),
        ('l',   'Liters (L)'),
        ('ml',  'Milliliters (mL)'),
        ('pcs', 'Pieces (pcs)'),
    ]

    user       = models.ForeignKey(User, on_delete=models.CASCADE)
    name       = models.CharField(max_length=200)
    quantity   = models.DecimalField(max_digits=10, decimal_places=2)
    unit       = models.CharField(max_length=10, choices=UNIT_CHOICES, default='g')
    date_added = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date_added']

    def __str__(self):
        return f"{self.name} — {self.quantity} {self.unit} ({self.user.username})"


class DailyMeal(models.Model):
    CATEGORY_CHOICES = [
        ('breakfast', 'Breakfast'),
        ('lunch',     'Lunch'),
        ('dinner',    'Dinner'),
        ('snacks',    'Snacks'),
    ]

    user       = models.ForeignKey(User, on_delete=models.CASCADE)
    name       = models.CharField(max_length=200)
    calories   = models.IntegerField()
    category   = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='breakfast')
    meal_date  = models.DateField()                    # the date the user selects
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['meal_date', 'category', 'created_at']

    def __str__(self):
        return f"{self.name} ({self.get_category_display()}) on {self.meal_date} — {self.user.username}"


# ── User Profile (Allergy Safety System) ─────────────────────────────────────

class UserProfile(models.Model):
    """One profile per user. Allergy keywords are stored in UserAllergy rows."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')

    # ── Medical Profile (managed by Manager only) ─────────────────────────────
    weight_kg     = models.DecimalField(
        max_digits=5, decimal_places=2,
        null=True, blank=True,
        help_text='Resident weight in kilograms',
    )
    height_cm     = models.DecimalField(
        max_digits=5, decimal_places=2,
        null=True, blank=True,
        help_text='Resident height in centimetres',
    )
    medical_notes = models.TextField(
        blank=True, default='',
        help_text='Any additional medical or dietary notes for this resident',
    )

    def __str__(self):
        return f"Profile of {self.user.username}"

    def get_allergy_keywords(self):
        """Returns a lowercase list of all allergy keywords for this user."""
        return list(
            self.allergies.values_list('keyword', flat=True)
        )


class UserAllergy(models.Model):
    """
    A single allergy keyword linked to a user profile.
    The keyword is matched (case-insensitive, substring) against food names.
    Examples: 'peanut', 'milk', 'shrimp', 'wheat', 'egg'
    """
    profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='allergies',
    )
    keyword = models.CharField(
        max_length=100,
        help_text='e.g. peanut, milk, shrimp, wheat, egg',
    )

    class Meta:
        verbose_name        = 'Allergy Keyword'
        verbose_name_plural = 'Allergy Keywords'
        unique_together = ('profile', 'keyword')
        ordering = ['keyword']

    def __str__(self):
        return self.keyword.capitalize()


# Auto-create a UserProfile whenever a new User is saved.
@receiver(post_save, sender=User)
def create_or_save_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
    else:
        # Ensure it exists for existing users (idempotent)
        UserProfile.objects.get_or_create(user=instance)

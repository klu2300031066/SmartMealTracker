from django.contrib import admin
from .models import UserProfile, UserAllergy, InventoryItem, DailyMeal, ManagerMessage


class UserAllergyInline(admin.TabularInline):
    """
    Renders a neat inline table of allergy keywords inside the
    User Profile admin page. Add a row per allergen keyword.
    """
    model       = UserAllergy
    extra       = 3          # show 3 empty rows ready to fill
    min_num     = 0
    fields      = ('keyword',)
    verbose_name        = 'Allergy Keyword'
    verbose_name_plural = 'Allergy Keywords'


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    inlines       = [UserAllergyInline]
    list_display  = ('user', 'allergy_keywords_display', 'weight_kg', 'height_cm')
    search_fields = ('user__username',)
    readonly_fields = ('user',)

    def allergy_keywords_display(self, obj):
        keywords = obj.get_allergy_keywords()
        if not keywords:
            return '—  (no allergies set)'
        return ', '.join(k.capitalize() for k in keywords)

    allergy_keywords_display.short_description = 'Allergy Keywords'


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'quantity', 'unit', 'user', 'date_added')
    list_filter = ('unit', 'user')
    search_fields = ('name', 'user__username')


@admin.register(DailyMeal)
class DailyMealAdmin(admin.ModelAdmin):
    list_display = ('name', 'calories', 'category', 'user', 'meal_date')
    list_filter = ('category', 'meal_date', 'user')
    search_fields = ('name', 'user__username')


@admin.register(ManagerMessage)
class ManagerMessageAdmin(admin.ModelAdmin):
    list_display = ('subject', 'sender', 'recipient', 'is_read', 'created_at')
    list_filter = ('is_read', 'sender', 'recipient')
    search_fields = ('subject', 'body')

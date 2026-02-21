from django.contrib import admin
from .models import UserProfile, UserAllergy


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
    list_display  = ('user', 'allergy_keywords_display')
    search_fields = ('user__username',)
    readonly_fields = ('user',)

    def allergy_keywords_display(self, obj):
        keywords = obj.get_allergy_keywords()
        if not keywords:
            return '—  (no allergies set)'
        return ', '.join(k.capitalize() for k in keywords)

    allergy_keywords_display.short_description = 'Allergy Keywords'

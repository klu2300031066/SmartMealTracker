from django.urls import path
from . import views

urlpatterns = [
    path('',                                        views.welcome,                name='welcome'),
    path('dashboard/',                              views.dashboard,              name='dashboard'),
    path('track-meals/',                            views.track_meals,            name='track_meals'),
    path('track-meals/delete/<int:meal_id>/',       views.delete_tracked_meal,    name='delete_tracked_meal'),
    path('inventory/',                              views.inventory,              name='inventory'),
    path('inventory/delete/<int:item_id>/',         views.delete_inventory_item,  name='delete_inventory_item'),
    path('inventory/update/<int:item_id>/',         views.update_inventory_item,  name='update_inventory_item'),
    path('signup/',                                 views.signup_view,            name='signup'),
    path('login/',                                  views.login_view,             name='login'),
    path('logout/',                                 views.logout_view,            name='logout'),
    path('delete/<int:meal_id>/',                   views.delete_meal,            name='delete_meal'),
    path('allergies/',                              views.manage_allergies,       name='manage_allergies'),
    path('allergies/delete/<int:allergy_id>/',       views.delete_allergy,         name='delete_allergy'),
    path('health-hub/',                             views.health_hub,             name='health_hub'),
    # ── Manager routes ────────────────────────────────────────────────────────
    path('manager/',                                views.manager_dashboard,         name='manager_dashboard'),
    path('manager/resident/<int:user_id>/',         views.edit_resident_profile,     name='edit_resident_profile'),
    path('manager/patient-food/',                   views.patient_food_info,         name='patient_food_info'),
    path('manager/send-review/',                    views.send_weekly_review,        name='send_weekly_review'),
    path('manager/user-inventory/',                 views.manager_inventory_search,  name='manager_inventory_search'),
    path('manager/resident/<int:user_id>/inventory/', views.manager_view_resident_inventory, name='manager_view_resident_inventory'),
    path('manager/resident/<int:resident_id>/export-pdf/', views.export_resident_pdf, name='export_resident_pdf'),
    # ── Resident routes ─────────────────────────────────────────────────────────────
    path('weekly-review/',                          views.weekly_review_inbox,       name='weekly_review_inbox'),
    # ── Gemini AI routes ───────────────────────────────────────────────────────────
    path('ai-meal/',                                views.ai_meal_page,              name='ai_meal_page'),
    path('ai-meal/generate/',                       views.generate_ai_meal,          name='generate_ai_meal'),
    path('ai-meal/confirm/',                        views.confirm_ai_meal,           name='confirm_ai_meal'),
]


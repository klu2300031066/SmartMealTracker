import os
import re
import json
import requests as http_requests
from dotenv import load_dotenv
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Sum
from django.utils import timezone
from .models import Meal, InventoryItem, DailyMeal, UserProfile, UserAllergy, ManagerMessage
from datetime import timedelta
import google.generativeai as genai
from .utils import render_to_pdf

# Load environment variables from .env file
load_dotenv()

# ── Gemini AI credentials ─────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

# ── Edamam credentials ────────────────────────────────────────────────────────
EDAMAM_APP_ID  = os.getenv('EDAMAM_APP_ID', '')
EDAMAM_APP_KEY = os.getenv('EDAMAM_APP_KEY', '')
EDAMAM_URL     = 'https://api.edamam.com/api/nutrition-data'


def _lookup_cached_calories(food_name: str) -> int | None:
    match = (
        Meal.objects
        .filter(name__iexact=food_name)
        .values_list('calories', flat=True)
        .first()
    )
    return match


def _call_edamam(food_name: str):
    try:
        resp = http_requests.get(
            EDAMAM_URL,
            params={
                'app_id':  EDAMAM_APP_ID,
                'app_key': EDAMAM_APP_KEY,
                'ingr':    food_name,
            },
            timeout=10,
        )
        if resp.status_code == 429:
            return None, '429'
        if resp.status_code in (404, 422):
            return None, 'not_found'
        if resp.status_code == 200:
            data   = resp.json()
            parsed = data.get('ingredients', [{}])[0].get('parsed', [])
            if not parsed:
                return None, 'not_found'
            kcal = round(parsed[0]['nutrients']['ENERC_KCAL']['quantity'])
            return kcal, None
        return None, 'error'
    except http_requests.exceptions.Timeout:
        return None, 'timeout'
    except http_requests.exceptions.RequestException:
        return None, 'error'


def _check_allergies_by_keyword(user, food_name: str) -> list:
    """
    Keyword-based allergy safety check.
    Fetches the user's allergy keywords from the DB and tests whether
    any keyword appears (case-insensitive, substring) in the food name.
    Returns a list of matched keyword strings (human-readable).
    """
    triggered = []
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return triggered

    keywords = profile.get_allergy_keywords()   # e.g. ['peanut', 'milk']
    food_lower = food_name.lower()
    for kw in keywords:
        if kw.lower() in food_lower:
            triggered.append(kw.capitalize())
    return triggered



def _get_health_suggestion(total_calories: int) -> dict:
    if total_calories == 0:
        return {
            'text': "You haven't logged any meals yet. Start adding food to get personalized insights!",
            'type': 'info',
            'emoji': '📝',
        }
    elif total_calories < 1200:
        return {
            'text': f"Only {total_calories} kcal today — that's quite low! Make sure you're eating enough to fuel your body.",
            'type': 'danger',
            'emoji': '⚠️',
        }
    elif total_calories < 1500:
        return {
            'text': f"{total_calories} kcal so far — a bit under your daily target. Try adding a healthy snack like nuts or fruit!",
            'type': 'warning',
            'emoji': '🍎',
        }
    elif total_calories <= 2200:
        return {
            'text': f"Perfect balance! {total_calories} kcal — you're right on track. Keep it up! 💪",
            'type': 'success',
            'emoji': '✅',
        }
    elif total_calories <= 2800:
        return {
            'text': f"{total_calories} kcal — you've gone a bit over. Consider a light walk or lighter next meal.",
            'type': 'warning',
            'emoji': '🚶',
        }
    else:
        return {
            'text': f"{total_calories} kcal — that's significantly over the recommended intake. Try balancing tomorrow!",
            'type': 'danger',
            'emoji': '🔴',
        }


# ── Welcome ───────────────────────────────────────────────────────────────────

def welcome(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'tracker/index.html')


# ── Helper: resolve calories (cache → API) ────────────────────────────────────

def _resolve_calories(request, meal_input, manual_cal_str, category):
    """
    Shared logic for resolving calories.
    Returns (calories: int | None, redirect_to: str).
    If calories is None, an error message was already set.
    """
    # Manual entry
    if manual_cal_str:
        try:
            cal = int(float(manual_cal_str))
            return cal, None
        except ValueError:
            messages.error(request, "Please enter a valid number for calories.")
            return None, 'error'

    # User cache
    user_cached = (
        Meal.objects
        .filter(user=request.user, name__iexact=meal_input)
        .values_list('calories', flat=True)
        .first()
    )
    if user_cached is not None:
        messages.success(request, f"✅ '{meal_input}' — {user_cached} kcal (from your history)")
        return user_cached, None

    # Global cache
    global_cached = _lookup_cached_calories(meal_input)
    if global_cached is not None:
        messages.success(request, f"✅ '{meal_input}' — {global_cached} kcal (from cache)")
        return global_cached, None

    # Edamam API
    calories, err = _call_edamam(meal_input)

    if err is None:
        messages.success(request, f"✅ '{meal_input}' — {calories} kcal (via Edamam API)")
        return calories, None
    elif err == '429':
        fuzzy = (
            Meal.objects
            .filter(name__icontains=meal_input.split()[0])
            .values_list('calories', flat=True)
            .first()
        )
        if fuzzy:
            messages.warning(request, f"⚠ API limit reached. Estimated: {fuzzy} kcal for '{meal_input}'.")
            return fuzzy, None
        else:
            messages.error(request, "API is resting. Wait 60s or enter calories manually!")
            return None, 'error'
    elif err == 'not_found':
        messages.error(request, "❓ Food not recognized. Try '300g chicken' format, or enter calories manually.")
        return None, 'error'
    elif err == 'timeout':
        messages.error(request, "⏱ API timed out. Enter calories manually or try again.")
        return None, 'error'
    else:
        messages.error(request, "⚠ API unavailable. Please enter calories manually.")
        return None, 'error'


# ── Dashboard (simple meal log) ───────────────────────────────────────────────

@login_required(login_url='login')
def dashboard(request):
    if request.method == 'POST':
        meal_input     = request.POST.get('meal_name', '').strip()
        manual_cal_str = request.POST.get('calories', '').strip()

        if not meal_input:
            messages.error(request, "Please enter a meal name.")
            return redirect('dashboard')

        cal, err = _resolve_calories(request, meal_input, manual_cal_str, 'breakfast')
        if cal is not None:
            Meal.objects.create(
                user=request.user,
                name=meal_input,
                calories=cal,
                category='breakfast',
            )
        return redirect('dashboard')

    meals          = Meal.objects.filter(user=request.user).order_by('-id')
    total_calories = sum(m.calories for m in meals)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    allergies   = profile.allergies.all()
    unread_count = ManagerMessage.objects.filter(
        recipient=request.user, is_read=False
    ).count()
    context = {
        'meals':          meals,
        'total_calories': total_calories,
        'allergies':      allergies,
        'profile':        profile,
        'unread_count':   unread_count,
    }
    return render(request, 'tracker/dashboard.html', context)


# ── Allergy Management ──────────────────────────────────────────────────────────────

@login_required(login_url='login')
def health_hub(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    context = {
        'profile': profile,
    }
    return render(request, 'tracker/health_hub.html', context)

@login_required(login_url='login')
def manage_allergies(request):
    """View-only profile for the logged-in user (Allergies, Height, Weight)."""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    # Residents can no longer modify allergies directly via this view
    if request.method == 'POST':
        messages.error(request, '🛡️ Profile changes must be requested through your care manager.')
        return redirect('manage_allergies')

    allergies = profile.allergies.all()
    context = {
        'profile': profile,
        'allergies': allergies,
    }
    return render(request, 'tracker/allergies.html', context)


@login_required(login_url='login')
def delete_allergy(request, allergy_id):
    """Entry point for deleting allergies — now disabled for residents."""
    messages.error(request, '🛡️ Profile modifications must be handled by your care manager.')
    return redirect('manage_allergies')


# ── Track Meals ───────────────────────────────────────────────────────────────

def _draft_key(user_id, date_str):
    """Session key for a user's draft meals for a given date."""
    return f'draft_meals_{user_id}_{date_str}'


@login_required(login_url='login')
def track_meals(request):
    today = timezone.localdate()
    from datetime import date as dt_date

    # Resolve date
    if request.method == 'POST':
        date_str = request.POST.get('meal_date', str(today))
    else:
        date_str = request.GET.get('date', str(today))

    try:
        selected_date = dt_date.fromisoformat(date_str)
    except (ValueError, TypeError):
        selected_date = today

    date_str = str(selected_date)
    skey = _draft_key(request.user.id, date_str)

    # ── POST actions ──────────────────────────────────────────────────────────
    if request.method == 'POST':
        action = request.POST.get('action', 'add')

        if action == 'add':
            meal_input     = request.POST.get('meal_name', '').strip()
            manual_cal_str = request.POST.get('calories', '').strip()
            category       = request.POST.get('category', 'breakfast')

            valid = [c[0] for c in DailyMeal.CATEGORY_CHOICES]
            if category not in valid:
                category = 'breakfast'

            if not meal_input:
                messages.error(request, 'Please enter a food item.')
                return redirect(f'/track-meals/?date={date_str}')

            cal, err = _resolve_calories(request, meal_input, manual_cal_str, category)
            if cal is not None:
                # ── Allergy Safety Check (keyword match) ──────────────────────
                # Checks food name against user's custom allergy keywords.
                # Works instantly — no API call needed.
                triggered_allergies = _check_allergies_by_keyword(request.user, meal_input)
                allergy_warning     = bool(triggered_allergies)

                draft = request.session.get(skey, [])
                draft.append({
                    'name':            meal_input,
                    'calories':        cal,
                    'category':        category,
                    'allergy_warning': allergy_warning,
                    'triggered':       triggered_allergies,
                })
                request.session[skey] = draft
                request.session.modified = True

                if allergy_warning:
                    # ── High-priority Medical Alert ───────────────────────────
                    # Manager-set allergies always produce this red warning.
                    messages.error(
                        request,
                        f'⚠️ Medical Alert: A manager has flagged this item as unsafe '
                        f'for your profile ({", ".join(triggered_allergies)} detected). '
                        f'Adding to draft — please consult your care manager.'
                    )
                else:
                    messages.success(request, f'"{meal_input}" added — click Save Day to store it.')

        elif action == 'remove_draft':
            idx = int(request.POST.get('draft_index', -1))
            draft = request.session.get(skey, [])
            if 0 <= idx < len(draft):
                removed = draft.pop(idx)
                request.session[skey] = draft
                request.session.modified = True
                messages.success(request, f'"{removed["name"]}" removed from draft.')

        elif action == 'save_day':
            draft = request.session.get(skey, [])
            if not draft:
                messages.error(request, 'Nothing to save — add some food first!')
            else:
                inventory_warnings = []
                items_updated = 0

                for item in draft:
                    # ── Persist the meal ───────────────────────────────────────
                    DailyMeal.objects.create(
                        user=request.user,
                        name=item['name'],
                        calories=item['calories'],
                        category=item['category'],
                        meal_date=selected_date,
                    )

                    # ── Smart Inventory Bridge ─────────────────────────────────
                    # Parse quantity and food name from the draft item name.
                    # Expected format examples: "300g chicken", "2 eggs", "500 ml milk"
                    # Strategy: split the name, try to extract a leading numeric token.
                    raw_name = item['name'].strip()
                    # Match an optional leading number (int or float) possibly
                    # attached to letters (e.g. "300g"), followed by the food name.
                    m = re.match(
                        r'^(\d+(?:\.\d+)?)\s*(?:g|kg|ml|l|pcs|piece|pieces|x)?\s+(.+)$',
                        raw_name, re.IGNORECASE
                    )
                    if m:
                        meal_qty   = float(m.group(1))
                        food_name  = m.group(2).strip()
                    else:
                        # No leading quantity found — treat the whole string as
                        # the food name and deduct 1 unit.
                        meal_qty  = 1
                        food_name = raw_name

                    inv_item = InventoryItem.objects.filter(
                        name__iexact=food_name,
                        user=request.user,
                    ).first()

                    if inv_item:
                        new_qty = float(inv_item.quantity) - meal_qty
                        if new_qty <= 0:
                            inv_item.quantity = 0
                            inventory_warnings.append(inv_item.name)
                        else:
                            inv_item.quantity = round(new_qty, 2)
                        inv_item.save()
                        items_updated += 1

                # Clear the draft session
                del request.session[skey]
                request.session.modified = True

                # ── Build feedback messages ────────────────────────────────────
                messages.success(
                    request,
                    f'✅ {len(draft)} meal(s) saved for '
                    f'{selected_date.strftime("%d %b %Y")} and Inventory updated!'
                )
                for food in inventory_warnings:
                    messages.warning(
                        request,
                        f'⚠️ Warning: You are out of {food}!'
                    )

        return redirect(f'/track-meals/?date={date_str}')

    # ── GET: build per-category data ──────────────────────────────────────────
    draft_items = request.session.get(skey, [])

    # Already-saved meals for this date (from previous Save Day calls)
    saved_meals = DailyMeal.objects.filter(user=request.user, meal_date=selected_date)

    cat_emojis = {'breakfast': '🌅', 'lunch': '☀️', 'dinner': '🌙', 'snacks': '🍿'}
    cat_labels = dict(DailyMeal.CATEGORY_CHOICES)

    # Build sections combining saved + draft
    category_sections = {}
    for key, label in DailyMeal.CATEGORY_CHOICES:
        saved_cat   = list(saved_meals.filter(category=key))
        draft_cat   = [
            {'name': d['name'], 'calories': d['calories'],
             'draft_index': i, 'is_draft': True}
            for i, d in enumerate(draft_items) if d['category'] == key
        ]
        saved_total = sum(m.calories for m in saved_cat)
        draft_total = sum(d['calories'] for d in draft_cat)
        category_sections[key] = {
            'label':       label,
            'emoji':       cat_emojis.get(key, '🍽️'),
            'saved_meals': saved_cat,
            'draft_meals': draft_cat,
            'total':       saved_total + draft_total,
            'saved_total': saved_total,
            'draft_total': draft_total,
        }

    total_calories = sum(s['total'] for s in category_sections.values())
    suggestion     = _get_health_suggestion(total_calories)
    max_cal        = max((s['total'] for s in category_sections.values()), default=1) or 1
    draft_count    = len(draft_items)

    context = {
        'category_sections': category_sections,
        'total_calories':    total_calories,
        'suggestion':        suggestion,
        'today':             today,
        'selected_date':     selected_date,
        'prev_day':          selected_date - timedelta(days=1),
        'next_day':          selected_date + timedelta(days=1),
        'max_cal':           max_cal,
        'draft_count':       draft_count,
        'date_str':          date_str,
    }
    return render(request, 'tracker/track_meals.html', context)


# ── Delete saved DailyMeal entry ──────────────────────────────────────────────

@login_required(login_url='login')
def delete_tracked_meal(request, meal_id):
    meal = DailyMeal.objects.get(id=meal_id, user=request.user)
    date = meal.meal_date
    meal.delete()

    return redirect(f"/track-meals/?date={date}")


# ── Manager Dashboard ─────────────────────────────────────────────────────────

@login_required(login_url='login')
def manager_dashboard(request):
    """Only accessible by staff (is_staff=True) — lists all non-staff residents."""
    if not request.user.is_staff:
        messages.error(request, '🚫 Access denied. Manager access only.')
        return redirect('dashboard')

    residents = (
        User.objects
        .filter(is_staff=False, is_superuser=False)
        .select_related('profile')
        .order_by('username')
    )
    # Ensure every resident has a profile
    for r in residents:
        UserProfile.objects.get_or_create(user=r)

    # Calculate stats for the dashboard
    residents_with_allergies_count = 0
    completed_profiles_count = 0
    for r in residents:
        if r.profile.allergies.exists():
            residents_with_allergies_count += 1
        if r.profile.weight_kg and r.profile.height_cm:
            completed_profiles_count += 1

    context = {
        'residents': residents,
        'residents_with_allergies_count': residents_with_allergies_count,
        'completed_profiles_count': completed_profiles_count,
    }
    return render(request, 'tracker/manager_dashboard.html', context)


# ── Edit Resident Profile (Manager only) ──────────────────────────────────────

@login_required(login_url='login')
def edit_resident_profile(request, user_id):
    """Manager edits a specific resident's medical profile and allergy keywords."""
    if not request.user.is_staff:
        messages.error(request, '🚫 Access denied. Manager access only.')
        return redirect('dashboard')

    resident = get_object_or_404(User, id=user_id, is_staff=False)
    profile, _ = UserProfile.objects.get_or_create(user=resident)

    if request.method == 'POST':
        action = request.POST.get('action', 'save_profile')

        if action == 'save_profile':
            # Weight
            weight_str = request.POST.get('weight_kg', '').strip()
            height_str = request.POST.get('height_cm', '').strip()
            notes      = request.POST.get('medical_notes', '').strip()

            try:
                profile.weight_kg = float(weight_str) if weight_str else None
            except ValueError:
                messages.error(request, 'Invalid weight value.')
                return redirect('edit_resident_profile', user_id=user_id)

            try:
                profile.height_cm = float(height_str) if height_str else None
            except ValueError:
                messages.error(request, 'Invalid height value.')
                return redirect('edit_resident_profile', user_id=user_id)

            profile.medical_notes = notes
            profile.save()
            messages.success(request, f"✅ Medical profile for {resident.username} updated successfully!")

        elif action == 'add_allergy':
            keyword = request.POST.get('keyword', '').strip().lower()
            if not keyword:
                messages.error(request, 'Please enter an allergy keyword.')
            elif len(keyword) > 100:
                messages.error(request, 'Keyword too long (max 100 characters).')
            else:
                _, created = UserAllergy.objects.get_or_create(profile=profile, keyword=keyword)
                if created:
                    messages.success(request, f'⚠️ Allergy "{keyword.capitalize()}" flagged for {resident.username}.')
                else:
                    messages.warning(request, f'"{keyword.capitalize()}" is already flagged.')

        elif action == 'delete_allergy':
            allergy_id = request.POST.get('allergy_id')
            try:
                allergy = UserAllergy.objects.get(id=allergy_id, profile=profile)
                name = allergy.keyword.capitalize()
                allergy.delete()
                messages.success(request, f'✅ Allergy "{name}" removed for {resident.username}.')
            except UserAllergy.DoesNotExist:
                pass

        return redirect('edit_resident_profile', user_id=user_id)

    allergies = profile.allergies.all()
    context = {
        'resident': resident,
        'profile':  profile,
        'allergies': allergies,
    }
    return render(request, 'tracker/edit_resident.html', context)


# ── Patient Food Info (Manager) ────────────────────────────────────────────────

@login_required(login_url='login')
def patient_food_info(request):
    """Manager views food statistics for a selected resident."""
    if not request.user.is_staff:
        messages.error(request, '🚫 Access denied. Manager access only.')
        return redirect('dashboard')

    residents = (
        User.objects
        .filter(is_staff=False, is_superuser=False)
        .order_by('username')
    )

    selected_resident = None
    stats = None

    resident_id = request.GET.get('resident')
    if resident_id:
        selected_resident = get_object_or_404(User, id=resident_id, is_staff=False)

        today = timezone.localdate()
        seven_days_ago = today - timedelta(days=6)

        # ── Daily calories for last 7 days ────────────────────────────────────
        daily_meals = DailyMeal.objects.filter(
            user=selected_resident,
            meal_date__range=[seven_days_ago, today],
        )

        # Build day-by-day calorie map
        day_labels = []
        day_calories = []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            cal = daily_meals.filter(meal_date=day).aggregate(total=Sum('calories'))['total'] or 0
            day_labels.append(day.strftime('%d %b'))
            day_calories.append(cal)

        # ── Category breakdown ────────────────────────────────────────────────
        category_totals = {}
        for meal in daily_meals:
            label = meal.get_category_display()
            category_totals[label] = category_totals.get(label, 0) + meal.calories

        # ── Top foods (most repeated) ─────────────────────────────────────────
        from django.db.models import Count
        top_foods = (
            DailyMeal.objects
            .filter(user=selected_resident)
            .values('name')
            .annotate(count=Count('id'), total_cal=Sum('calories'))
            .order_by('-count')[:8]
        )

        # ── Summary numbers ───────────────────────────────────────────────────
        total_cal_7d   = sum(day_calories)
        avg_daily_cal  = round(total_cal_7d / 7)
        total_meals_7d = daily_meals.count()
        most_active_day = day_labels[day_calories.index(max(day_calories))] if day_calories else '—'

        # ── Recent meal log ───────────────────────────────────────────────────
        recent_meals = daily_meals.order_by('-meal_date', '-created_at')[:20]

        stats = {
            'day_labels':      json.dumps(day_labels),
            'day_calories':    json.dumps(day_calories),
            'category_labels': json.dumps(list(category_totals.keys())),
            'category_data':   json.dumps(list(category_totals.values())),
            'top_foods':       top_foods,
            'total_cal_7d':    total_cal_7d,
            'avg_daily_cal':   avg_daily_cal,
            'total_meals_7d':  total_meals_7d,
            'most_active_day': most_active_day,
            'recent_meals':    recent_meals,
        }

    # Pass profile + allergies regardless (only populated if resident selected)
    res_profile  = None
    res_allergies = []
    if selected_resident:
        res_profile, _ = UserProfile.objects.get_or_create(user=selected_resident)
        res_allergies   = res_profile.allergies.all()

    context = {
        'residents':         residents,
        'selected_resident': selected_resident,
        'selected_resident_id': str(selected_resident.id) if selected_resident else None,
        'stats':             stats,
        'res_profile':       res_profile,
        'res_allergies':     res_allergies,
    }
    return render(request, 'tracker/patient_food_info.html', context)


@login_required(login_url='login')
def export_resident_pdf(request, resident_id):
    """
    Generates a PDF medical report. Managers can see any resident,
    residents can only see their own.
    """
    # Security: Residents can only view themselves. Managers can view anyone.
    if not request.user.is_staff and request.user.id != int(resident_id):
        messages.error(request, '🚫 Access denied. You can only export your own report.')
        return redirect('dashboard')


    resident = get_object_or_404(User, id=resident_id, is_staff=False)
    profile, _ = UserProfile.objects.get_or_create(user=resident)

    # Data for the last 7 days
    today = timezone.localdate()
    seven_days_ago = today - timedelta(days=6)

    meals = DailyMeal.objects.filter(
        user=resident,
        meal_date__range=[seven_days_ago, today]
    ).order_by('-meal_date', 'category')

    # Calculate average calories
    daily_totals = meals.values('meal_date').annotate(day_total=Sum('calories'))
    total_cal_sum = sum(d['day_total'] for d in daily_totals)
    avg_calories = round(total_cal_sum / 7)

    inventory = InventoryItem.objects.filter(user=resident)
    allergies = profile.allergies.all()

    context = {
        'resident': resident,
        'profile': profile,
        'meals': meals,
        'avg_calories': avg_calories,
        'inventory': inventory,
        'allergies': allergies,
        'today': today,
    }

    response = render_to_pdf('tracker/medical_report.html', context)
    if response:
        filename = f"Medical_Report_{resident.username}_{today.strftime('%Y-%m-%d')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    return HttpResponse("Error generating PDF", status=400)



# ── Messaging: Manager → Resident ─────────────────────────────────────────────

@login_required(login_url='login')
def send_weekly_review(request):
    """Manager composes and sends a message/weekly review to a resident."""
    if not request.user.is_staff:
        messages.error(request, '🚫 Manager access only.')
        return redirect('dashboard')

    residents = User.objects.filter(is_staff=False, is_superuser=False).order_by('username')

    if request.method == 'POST':
        recipient_id = request.POST.get('recipient')
        subject      = request.POST.get('subject', '').strip() or 'Weekly Review'
        body         = request.POST.get('body', '').strip()

        if not recipient_id or not body:
            messages.error(request, 'Please select a resident and write a message.')
        else:
            recipient = get_object_or_404(User, id=recipient_id, is_staff=False)
            ManagerMessage.objects.create(
                sender=request.user,
                recipient=recipient,
                subject=subject,
                body=body,
            )
            messages.success(request, f'✅ Message sent to {recipient.username}!')
            return redirect('send_weekly_review')

    preselect     = request.GET.get('resident')
    sent_messages = ManagerMessage.objects.filter(sender=request.user).order_by('-created_at')[:30]

    context = {
        'residents':     residents,
        'preselect':     preselect,
        'sent_messages': sent_messages,
    }
    return render(request, 'tracker/send_weekly_review.html', context)


@login_required(login_url='login')
def weekly_review_inbox(request):
    """Resident views messages sent by their manager — marks all as read on open."""
    if request.user.is_staff:
        return redirect('manager_dashboard')

    inbox = ManagerMessage.objects.filter(recipient=request.user)
    inbox.filter(is_read=False).update(is_read=True)   # mark all read

    context = {'inbox': inbox}
    return render(request, 'tracker/weekly_review_inbox.html', context)


# ── Sign Up ───────────────────────────────────────────────────────────────────

def signup_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('dashboard')
    else:
        form = UserCreationForm()
    return render(request, 'tracker/signup.html', {'form': form})


# ── Login ─────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('manager_dashboard' if request.user.is_staff else 'dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            # Staff → Manager Dashboard, residents → regular Dashboard
            return redirect('manager_dashboard' if user.is_staff else 'dashboard')
    else:
        form = AuthenticationForm()
    return render(request, 'tracker/login.html', {'form': form})


# ── Logout ────────────────────────────────────────────────────────────────────

def logout_view(request):
    logout(request)
    return redirect('login')


# ── Delete Meal (from dashboard) ─────────────────────────────────────────────

@login_required(login_url='login')
def delete_meal(request, meal_id):
    meal = Meal.objects.get(id=meal_id, user=request.user)
    meal.delete()
    return redirect('dashboard')


# ── Inventory ─────────────────────────────────────────────────────────────────

@login_required(login_url='login')
def inventory(request):
    if request.method == 'POST':
        name     = request.POST.get('name', '').strip()
        quantity = request.POST.get('quantity', '').strip()
        unit     = request.POST.get('unit', 'g')

        valid_units = [u[0] for u in InventoryItem.UNIT_CHOICES]
        if unit not in valid_units:
            unit = 'g'

        if not name:
            messages.error(request, 'Please enter an item name.')
            return redirect('inventory')
        try:
            qty = float(quantity)
            if qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, 'Please enter a valid quantity.')
            return redirect('inventory')

        InventoryItem.objects.create(
            user=request.user,
            name=name,
            quantity=qty,
            unit=unit,
        )
        messages.success(request, f"'{name}' added to inventory!")
        return redirect('inventory')

    items = InventoryItem.objects.filter(user=request.user)
    context = {
        'items':       items,
        'unit_choices': InventoryItem.UNIT_CHOICES,
    }
    return render(request, 'tracker/inventory.html', context)


@login_required(login_url='login')
def delete_inventory_item(request, item_id):
    item = InventoryItem.objects.get(id=item_id, user=request.user)
    item.delete()
    return redirect('inventory')


@login_required(login_url='login')
def update_inventory_item(request, item_id):
    if request.method == 'POST':
        item     = InventoryItem.objects.get(id=item_id, user=request.user)
        quantity = request.POST.get('quantity', '').strip()
        unit     = request.POST.get('unit', item.unit)

        valid_units = [u[0] for u in InventoryItem.UNIT_CHOICES]
        if unit not in valid_units:
            unit = item.unit

        try:
            qty = float(quantity)
            if qty < 0:
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, 'Please enter a valid quantity.')
            return redirect('inventory')

        item.quantity = qty
        item.unit     = unit
        item.save()
        messages.success(request, f"'{item.name}' updated to {qty} {unit}.")
    return redirect('inventory')


# ── AI Meal: Standalone Page ─────────────────────────────────────────────────

@login_required(login_url='login')
def ai_meal_page(request):
    """Renders the dedicated AI Meal Generator page."""
    return render(request, 'tracker/ai_meal.html')


# ── AI: Generate Meal Recipe (Gemini) ─────────────────────────────────────────

@login_required(login_url='login')
def generate_ai_meal(request):
    """POST — Calls Gemini to generate a zero-waste recipe from the user's inventory."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # 1. Gather available inventory items (quantity > 0)
    inventory_items = InventoryItem.objects.filter(user=request.user, quantity__gt=0)
    ingredients_list = [
        f"{float(item.quantity):g} {item.unit} {item.name}"
        for item in inventory_items
    ]

    if not ingredients_list:
        return JsonResponse(
            {'error': 'Your inventory is empty! Add some ingredients first before generating a recipe.'},
            status=400
        )

    # 2. Gather allergy keywords
    try:
        profile = request.user.profile
        allergy_keywords = profile.get_allergy_keywords()
    except UserProfile.DoesNotExist:
        allergy_keywords = []

    # 3. Calculate remaining calorie budget for today
    today = timezone.localdate()
    calories_today = (
        DailyMeal.objects
        .filter(user=request.user, meal_date=today)
        .aggregate(total=Sum('calories'))['total'] or 0
    )
    calorie_target   = 2000
    remaining_cal    = max(0, calorie_target - calories_today)

    # 4. Build structured Gemini prompt
    prompt = f"""System: You are a professional Zero-Waste Chef for a care facility.
Available Ingredients: {', '.join(ingredients_list)}.
Resident Allergies: {', '.join(allergy_keywords) if allergy_keywords else 'None'}.
Calorie Target (remaining today): {remaining_cal} kcal.
Task: Generate a simple 1-meal recipe name and 3 bullet points of instructions.
Use ONLY available ingredients. Do NOT use allergens. Keep it simple and nutritious.

Respond ONLY in this exact JSON format (no markdown, no extra text):
{{
  "recipe_name": "Name of the dish",
  "estimated_calories": 450,
  "instructions": [
    "First instruction step",
    "Second instruction step",
    "Third instruction step"
  ],
  "ingredients_used": [
    {{"name": "ingredient name exactly as listed", "quantity": 100, "unit": "g"}}
  ]
}}"""

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # Use the latest Gemini 3 preview model as requested
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # Build prompt data
        inventory_str = ', '.join(ingredients_list)
        allergy_str   = ', '.join(allergy_keywords) if allergy_keywords else 'None'

        # Debug print requested to verify data flow
        print(f"DEBUG — AI Generation Context: Inventory: [{inventory_str}], Allergies: [{allergy_str}], Budget: {remaining_cal} kcal")
        
        # We still need JSON for the frontend to work
        final_prompt = f"""Using ONLY these ingredients: {inventory_str}, suggest a recipe. 
EXCLUDE these allergens: {allergy_str}. 
Keep it under {remaining_cal} calories. 
Return a short Name and 3 Steps.

Respond ONLY in JSON:
{{
  "recipe_name": "Name",
  "estimated_calories": integer,
  "instructions": ["Step 1", "Step 2", "Step 3"],
  "ingredients_used": [{{"name": "item", "quantity": 0, "unit": "unit"}}]
}}"""

        response = model.generate_content(final_prompt)
        raw_text = response.text.strip()

        # Handle potential markdown fences
        if raw_text.startswith('```'):
            parts = raw_text.split('```')
            raw_text = parts[1]
            if raw_text.startswith('json'):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        recipe_data = json.loads(raw_text)

        # Persist in session
        request.session['ai_recipe'] = recipe_data
        request.session.modified     = True

        return JsonResponse({'success': True, 'recipe': recipe_data})

    except Exception as e:
        # User requested specific fallback message
        return JsonResponse(
            {'error': '⚠️ Chef is busy! Try again in a moment.'},
            status=500
        )


# ── AI: Confirm / Cook / Deduct Ingredients ──────────────────────────────────

@login_required(login_url='login')
def confirm_ai_meal(request):
    """POST — Deducts AI-recipe ingredients from inventory and logs the meal."""
    if request.method != 'POST':
        return redirect('dashboard')

    recipe = request.session.get('ai_recipe')
    if not recipe:
        messages.error(request, '⚠️ No AI recipe found in session. Generate one first!')
        return redirect('dashboard')

    ingredients_used = recipe.get('ingredients_used', [])
    cooked           = []
    low_stock        = []

    for ing in ingredients_used:
        name = ing.get('name', '').strip()
        qty  = float(ing.get('quantity', 1))

        # Exact match first, then partial
        inv_item = InventoryItem.objects.filter(
            user=request.user, name__iexact=name
        ).first()
        if not inv_item:
            inv_item = InventoryItem.objects.filter(
                user=request.user, name__icontains=name
            ).first()

        if inv_item:
            new_qty = float(inv_item.quantity) - qty
            if new_qty <= 0:
                inv_item.quantity = 0
                low_stock.append(inv_item.name)
            else:
                inv_item.quantity = round(new_qty, 2)
            inv_item.save()
            cooked.append(inv_item.name)

    # Log the cooked meal as today's DailyMeal
    recipe_name    = recipe.get('recipe_name', 'AI Generated Meal')
    estimated_cal  = int(recipe.get('estimated_calories', 0))
    today          = timezone.localdate()

    DailyMeal.objects.create(
        user      = request.user,
        name      = recipe_name,
        calories  = estimated_cal,
        category  = 'lunch',
        meal_date = today,
    )

    # Clear session
    request.session.pop('ai_recipe', None)
    request.session.modified = True

    messages.success(
        request,
        f'🍳 "{recipe_name}" cooked! Logged {estimated_cal} kcal and updated {len(cooked)} inventory item(s).'
    )
    for food in low_stock:
        messages.warning(request, f'⚠️ {food} is now out of stock!')

    return redirect('track_meals')

# ── Manager: User Inventory Search ───────────────────────────────────────────

@login_required(login_url='login')
def manager_inventory_search(request):
    """Manager views a list of all residents to select one for inventory check."""
    if not request.user.is_staff:
        messages.error(request, '🚫 Access denied. Manager access only.')
        return redirect('dashboard')

    residents = User.objects.filter(is_staff=False, is_superuser=False).order_by('username')
    return render(request, 'tracker/manager_inventory_search.html', {'residents': residents})


# ── Manager: View Resident Inventory ──────────────────────────────────────────

@login_required(login_url='login')
def manager_view_resident_inventory(request, user_id):
    """Manager views a specific resident's current inventory items."""
    if not request.user.is_staff:
        messages.error(request, '🚫 Access denied. Manager access only.')
        return redirect('dashboard')

    resident = get_object_or_404(User, id=user_id, is_staff=False)
    # Get all inventory items for this specific resident
    inventory_items = InventoryItem.objects.filter(user=resident).order_by('name')

    context = {
        'resident': resident,
        'inventory_items': inventory_items,
    }
    return render(request, 'tracker/manager_resident_inventory.html', context)

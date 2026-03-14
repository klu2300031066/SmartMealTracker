from .models import SystemSettings

def system_settings(request):
    try:
        settings = SystemSettings.get_settings()
        return {'system_settings': settings}
    except Exception:
        # In case the database isn't fully migrated yet
        return {'system_settings': None}

"""
Middleware to handle maintenance mode.
If enabled, non-superusers will be redirected to the maintenance page.
"""

from django.shortcuts import render
from django.urls import reverse
from .models import SystemSettings

class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        
        # Paths that should always be accessible even during maintenance
        allowed_paths = [
            reverse('login'),
            reverse('logout'),
            reverse('admin_welcome'),
            # You might want Django admin accessible as well
            '/admin/',
        ]
        
        # Don't block superusers/staff from accessing the site (optional, here we block non-superusers)
        if hasattr(request, 'user') and request.user.is_authenticated:
            if request.user.is_superuser:
                return self.get_response(request)

        # Allow standard admin login page as well
        if path.startswith('/admin/'):
            return self.get_response(request)

        if path not in allowed_paths:
            try:
                # Fast check for singleton settings object
                settings = SystemSettings.get_settings()
                if settings.is_maintenance_mode:
                    return render(request, 'tracker/maintenance.html', status=503)
            except Exception:
                pass # E.g., tables not migrated yet
                
        return self.get_response(request)

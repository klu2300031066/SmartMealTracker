from django.core.management.base import BaseCommand
from tracker.models import SystemSettings

class Command(BaseCommand):
    help = 'Toggles or sets the system maintenance mode'

    def add_arguments(self, parser):
        parser.add_argument('state', nargs='?', choices=['on', 'off'], help='Explicitly set to "on" or "off"')

    def handle(self, *args, **kwargs):
        state = kwargs['state']
        settings = SystemSettings.get_settings()
        
        if state == 'on':
            settings.is_maintenance_mode = True
            msg = 'ENABLED'
        elif state == 'off':
            settings.is_maintenance_mode = False
            msg = 'DISABLED'
        else:
            settings.is_maintenance_mode = not settings.is_maintenance_mode
            msg = 'ENABLED' if settings.is_maintenance_mode else 'DISABLED'
            
        settings.save()
        
        # Use success coloring for off, warning for on
        style = self.style.SUCCESS if msg == 'DISABLED' else self.style.WARNING
        self.stdout.write(style(f'System maintenance mode is now {msg}'))

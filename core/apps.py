from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from corsheaders.signals import check_request_enabled

        from .cors import check_request_enabled as handler

        check_request_enabled.connect(handler, dispatch_uid="core.cors.public_widget")

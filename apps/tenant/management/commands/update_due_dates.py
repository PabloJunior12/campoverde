from django.core.management.base import BaseCommand
from apps.tramite.models import Procedure
from apps.tramite.utils import calculate_due_date

class Command(BaseCommand):
    
    help = "Actualiza las fechas de vencimiento existentes"

    def handle(self, *args, **kwargs):
        procedures = Procedure.objects.all()

        for procedure in procedures:
            procedure.due_date = calculate_due_date(procedure.created_at)
            procedure.save(update_fields=["due_date"])

        self.stdout.write(self.style.SUCCESS("Fechas actualizadas correctamente"))
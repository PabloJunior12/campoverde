from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.timezone import now

from apps.tramite.models import ProcedureFlow
from apps.tramite.utils import (
    check_schedule,
    ScheduleResult
)

class Command(BaseCommand):

    help = "Procesa tramites registrados fuera de horario laboral"

    @transaction.atomic
    def handle(self, *args, **options):

        current_status = check_schedule(now())

        # Si aún no estamos en horario laboral, no hacer nada
        if current_status != ScheduleResult.IN_SCHEDULE:
            self.stdout.write(
                self.style.WARNING(
                    "Aún fuera de horario laboral. No se procesaron tramites."
                )
            )
            return

        # Buscar pendientes
        pending_flows = ProcedureFlow.objects.select_related(
            "procedure", "to_area"
        ).filter(
            status=ProcedureFlow.PENDING_SCHEDULE,
            is_active=True
        )

        processed = 0

        for flow in pending_flows:
            flow.status = ProcedureFlow.SENT
            flow.sent_at = now()
            flow.save(update_fields=["status", "sent_at"])
            processed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Trámites procesados correctamente: {processed}"
            )
        )

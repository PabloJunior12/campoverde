
import re

from django.core.management.base import BaseCommand
from django.db import connections
from django.utils.timezone import make_aware
from apps.tramite.models import Procedure, ProcedureFlow, Area
from apps.user.models import User

STATUS_MAP = {
    "E": ProcedureFlow.SENT,
    "R": ProcedureFlow.RECEIVED,
    "A": ProcedureFlow.FINALIZED,
    "Por finalizar": ProcedureFlow.SENT,
    "O": ProcedureFlow.OBSERVED,
    "Z": ProcedureFlow.REJECTED,
}

FINAL_STATES = {"Finalizado"}


def parse_origin_options(value):

    if not value:
        return []

    if isinstance(value, list):
        return value

    return [x.strip() for x in str(value).split(",") if x.strip()]


class Command(BaseCommand):

    help = "Remigrar ProcedureFlow faltantes de trámites 2025"

    def handle(self, *args, **options):

        self.stdout.write(
            self.style.WARNING(
                "Iniciando remigración segura de flows 2025..."
            )
        )

        with connections['legacy'].cursor() as cursor:

            cursor.execute("""

                SELECT
                    h.*,
                    t.codigo AS tramite_codigo,

                    ao.initials AS origen_initials,
                    ad.initials AS destino_initials,
                    ad.type_tramite AS destino_type

                FROM historicos h

                INNER JOIN tramites t
                    ON t.id = h.tramite_id

                LEFT JOIN areas ao
                    ON ao.id = h.origen_id

                LEFT JOIN areas ad
                    ON ad.id = h.destino_id

                WHERE t.codigo LIKE '%-2025'

                ORDER BY h.tramite_id, h.secuencia

            """)

            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()

        total = len(rows)

        self.stdout.write(
            self.style.SUCCESS(
                f"Registros encontrados: {total}"
            )
        )

        user = User.objects.first()

        migrated = 0
        skipped = 0
        errors = 0

        processed_procedures = set()

        for row in rows:

            data = dict(zip(columns, row))

            raw_code = data["codigo"]

            normalized_code = re.sub(
                r"-C\d+$",
                "",
                raw_code
            )

            try:

                # =========================================
                # BUSCAR PROCEDURE
                # =========================================

                procedure = Procedure.objects.filter(
                    code=normalized_code,
                    tramite_type=data["tipo_tramite"]
                ).first()

    
                if not procedure:

                    self.stdout.write(
                        self.style.ERROR(
                            f"No existe Procedure: "
                            f"{normalized_code}"
                        )
                    )

                    errors += 1
                    continue

                # =========================================
                # VALIDAR SI YA TIENE FLOWS
                # =========================================

                cache_key = (
                    procedure.id,
                    procedure.tramite_type
                )

                if cache_key not in processed_procedures:

                    already_has_flows = ProcedureFlow.objects.filter(
                        procedure=procedure
                    ).exists()

                    if already_has_flows:

                        self.stdout.write(
                            self.style.WARNING(
                                f"Ya tiene flows: "
                                f"{procedure.code}"
                            )
                        )

                        processed_procedures.add(cache_key)

                        skipped += 1
                        continue
                           
                    processed_procedures.add(cache_key)

                from_area = Area.objects.filter(
                    initials__iexact=data["origen_initials"]
                ).first()

                first_area = Area.objects.order_by("id").first()

                to_area = Area.objects.filter(
                    initials__iexact=data["destino_initials"]
                ).first()

                if data["tipo_tramite"] == 'TV' and data["secuencia"] <= 2:

                    from_area_final = first_area

                else:

                    from_area_final = from_area

                if not to_area:

                    self.stdout.write(
                        self.style.ERROR(
                            f"Área destino no encontrada: "
                            f"{normalized_code}"
                        )
                    )

                    errors += 1
                    continue

                origen_asunto = data.get("origen_asunto")
                procedure_subject = procedure.subject

                subject_derivar = None

                if (
                    origen_asunto
                    and procedure_subject
                    and origen_asunto.strip() != procedure_subject.strip()
                ):
                    subject_derivar = origen_asunto

                is_derive = (
                    data["secuencia"] > 3
                    or (
                        data["secuencia"] == 3
                        and data["estado_tramite"] not in FINAL_STATES
                    )
                )

                origin_options = parse_origin_options(
                    data.get("destino_asunto")
                )

                if data["estado_tramite"] == "Observado":

                    comment = data.get("observacion")

                else:

                    comment = data.get("comentario")

                flow_type = ProcedureFlow.NORMAL

                status = STATUS_MAP.get(
                    data["estado_tramite"],
                    ProcedureFlow.SENT
                )

                is_active = (data["estado"] == "V")

                flow = ProcedureFlow.objects.create(

                    procedure=procedure,

                    from_area=from_area_final,
                    to_area=to_area,

                    flow_type=flow_type,

                    status=status,

                    subject=procedure.subject,
                    subject_derivar=subject_derivar,

                    comment=comment,

                    sent_by=user,

                    sequence=data["secuencia"],

                    origin_options=origin_options,

                    is_active=is_active,

                    is_to_finalize=(
                        data["estado_tramite"] == "Por finalizar"
                        or data["operacion"] == "PF"
                    ),

                    is_derive=is_derive
                )

                if data.get("created_at"):

                    flow.created_at = make_aware(
                        data["created_at"]
                    )

                    flow.save(update_fields=["created_at"])

                migrated += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Flow migrado: "
                        f"{procedure.code} "
                        f"Secuencia: {data['secuencia']}"
                    )
                )

            except Exception as e:

                errors += 1

                self.stdout.write(
                    self.style.ERROR(
                        f"Error en {normalized_code}: {str(e)}"
                    )
                )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "===================================="
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Flows migrados: {migrated}"
            )
        )

        self.stdout.write(
            self.style.WARNING(
                f"Omitidos: {skipped}"
            )
        )

        self.stdout.write(
            self.style.ERROR(
                f"Errores: {errors}"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Proceso finalizado"
            )
        )


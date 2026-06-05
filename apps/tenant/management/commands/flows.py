from django.core.management.base import BaseCommand
from django.db import connections
from apps.tramite.models import Procedure, ProcedureFlow, Area
from apps.user.models import User
from django.utils.timezone import make_aware
from apps.tenant.utils import parse_origin_options
from collections import defaultdict
import re


STATUS_MAP = {
    "Enviado": ProcedureFlow.SENT,
    "Recepcionado": ProcedureFlow.RECEIVED,
    "Finalizado": ProcedureFlow.FINALIZED,
    "Por finalizar": ProcedureFlow.SENT,
    "Observado": ProcedureFlow.OBSERVED,
    "Rechazado": ProcedureFlow.REJECTED,
}

FINAL_STATES = {"Finalizado"}

class Command(BaseCommand):
    help = "Migrar historial de trámites (ProcedureFlow)"

    def handle(self, *args, **options):
        with connections['legacy'].cursor() as cursor:
            cursor.execute("""
                SELECT
                    h.*,
                    t.codigo AS tramite_codigo,
                    t.agency_id AS tramite_agency_id,
                    ao.initials AS origen_initials,
                    ad.initials AS destino_initials,
                    ad.type_tramite AS destino_type, 
                    u.username AS username
                FROM historicos h
                INNER JOIN tramites t ON t.id = h.tramite_id
                LEFT JOIN areas ao ON ao.id = h.origen_id
                LEFT JOIN areas ad ON ad.id = h.destino_id
                LEFT JOIN users u ON u.id = h.user_id
              
                ORDER BY h.tramite_id, h.secuencia
              
            """)

            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            total = len(rows)


        for row in rows:

            data = dict(zip(columns, row))
            
            raw_code = data["codigo"]
            normalized_code = re.sub(r"-C\d+$", "", raw_code)

            try:    
                procedure = Procedure.objects.get(
                    code=normalized_code,
                    agency_id=data["tramite_agency_id"]
                )
            except Procedure.DoesNotExist:

                continue

            from_area = Area.objects.filter(
                initials__iexact=data["origen_initials"]
            ).first()

            first_area = Area.objects.order_by("id").first()    
            
            to_area = Area.objects.filter(initials__iexact=data["destino_initials"]).first()

            if data["tipo_tramite"] == 'TV' and data["secuencia"] <= 2:

                from_area_final = first_area

            else:

                
                from_area_final = from_area

            user = User.objects.filter(
                username=data["username"]
            ).first()


            if not to_area or not user:

                continue

            origen_asunto = data.get("origen_asunto")
            procedure_subject = procedure.subject

            subject_derivar = None

            if origen_asunto and origen_asunto.strip() != procedure_subject.strip():
                subject_derivar = origen_asunto
            
            is_derive = (
                data["secuencia"] > 3
                or (
                    data["secuencia"] == 3
                    and data["estado_tramite"] not in FINAL_STATES
                )
            )

            origin_options = parse_origin_options(data.get("destino_asunto"))

            if data["estado_tramite"] == "Observado":
               
               comment = data.get("observacion")

            else:
               
               comment = data.get("comentario")

            # =====================================================
            # FLOW TYPE + STATUS
            # =====================================================
            if data["solo_visualizacion"] == 1:
                flow_type = ProcedureFlow.COPY

                if data["operacion_tramite"] == "RZ":
                    status = ProcedureFlow.REJECTED
                elif data["operacion_tramite"] == "CP":
                    status = ProcedureFlow.SENT
                else:
                    status = ProcedureFlow.RECEIVED

            else:

                flow_type = ProcedureFlow.NORMAL

                if (data["estado_tramite"] == "Por finalizar" and data["operacion_tramite"] == "PT"):

                    status = ProcedureFlow.RECEIVED

                else:
                    
                    status = STATUS_MAP.get(data["estado_tramite"], ProcedureFlow.SENT)

            is_active = (
                    data["estado"] == "V"
                    or data["estado_tramite"] == "Observado"
            )

            procedure = ProcedureFlow.objects.create(
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
                is_to_finalize = data["estado_tramite"] == "Por finalizar" or data["operacion"] == "PF",
                is_derive = is_derive
            )

            procedure.created_at = make_aware(data["created_at"])
            procedure.save(update_fields=["created_at"])
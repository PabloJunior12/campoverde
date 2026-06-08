from django.core.management.base import BaseCommand
from django.db import connections
from apps.tramite.models import Procedure, ProcedureFlow, Area
from apps.user.models import User
from django.utils.timezone import make_aware
from apps.tenant.utils import parse_origin_options
from collections import defaultdict
import re


STATUS_MAP = {
    "E": ProcedureFlow.SENT,
    "R": ProcedureFlow.RECEIVED,
    "A": ProcedureFlow.FINALIZED,
    "Por finalizar": ProcedureFlow.SENT,
    "O": ProcedureFlow.OBSERVED,
    "Z": ProcedureFlow.REJECTED,
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
                 
                    ao.initials AS origen_initials,
                    ad.initials AS destino_initials,
                    ad.type_tramite AS destino_type
                   
                FROM historicos h
                INNER JOIN tramites t ON t.id = h.tramite_id
                LEFT JOIN areas ao ON ao.id = h.origen_id
                LEFT JOIN areas ad ON ad.id = h.destino_id
      
                WHERE YEAR(t.created_at) = 2026    
                           
                ORDER BY h.tramite_id, h.secuencia
                           
              
            """)

            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            total = len(rows)

        user = User.objects.first()
        for row in rows:

            data = dict(zip(columns, row))
            
            raw_code = data["codigo"]
            normalized_code = re.sub(r"-C\d+$", "", raw_code)

            try:    
                procedure = Procedure.objects.get(
                    code=normalized_code,
                    from_area__type=data["tipo_tramite"]
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

            if not to_area:

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
   
            flow_type = ProcedureFlow.NORMAL

                    
            status = STATUS_MAP.get(data["estado_tramite"], ProcedureFlow.SENT)

            is_active = (data["estado"] == "V")

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
import re

from django.core.management.base import BaseCommand
from django.db import connections
from django.utils.timezone import make_aware

from apps.tramite.models import (
    Procedure,
    ProcedureFlow,
    Area,
)
from apps.user.models import User


# ============================================================
# ESTADOS DEL SISTEMA LEGACY
# ============================================================

STATUS_MAP = {
    "E": ProcedureFlow.SENT,
    "R": ProcedureFlow.RECEIVED,
    "A": ProcedureFlow.FINALIZED,

    "Por finalizar": ProcedureFlow.SENT,

    "O": ProcedureFlow.OBSERVED,
    "Z": ProcedureFlow.REJECTED,
}


FINAL_STATES = {
    "Finalizado"
}


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def parse_origin_options(value):

    if not value:
        return []

    if isinstance(value, list):
        return value

    return [
        x.strip()
        for x in str(value).split(",")
        if x.strip()
    ]


def normalize_code(value):

    if not value:
        return value

    return re.sub(
        r"-C\d+$",
        "",
        str(value).strip()
    )


# ============================================================
# COMMAND
# ============================================================

class Command(BaseCommand):

    help = (
        "Remigrar ProcedureFlow históricos de Procedures "
        "del área 48 para trámites TE/TI"
    )

    def handle(self, *args, **options):

        self.stdout.write("")

        self.stdout.write(
            self.style.WARNING(
                "=============================================="
            )
        )

        self.stdout.write(
            self.style.WARNING(
                "INICIANDO REMIGRACIÓN DE HISTÓRICOS"
            )
        )

        self.stdout.write(
            self.style.WARNING(
                "Área origen: 48 | Tipos: TE / TI"
            )
        )

        self.stdout.write(
            self.style.WARNING(
                "=============================================="
            )
        )

        # =====================================================
        # 1. OBTENER PROCEDURES DEL ÁREA 48
        #    SOLAMENTE LOS QUE NO TIENEN FLOWS
        # =====================================================

        procedures = list(
            Procedure.objects.filter(
                # pk = "22844",
                from_area_id=48,
                tramite_type__in=["TE", "TI"],
                flows__isnull=True
            )
            .distinct()
            .only(
                "id",
                "code",
                "tramite_type",
                "subject",
                "from_area",
            )
        )

        total_procedures = len(procedures)

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                f"Procedures encontrados: "
                f"{total_procedures}"
            )
        )

        if not procedures:

            self.stdout.write(
                self.style.WARNING(
                    "No existen Procedures para procesar."
                )
            )

            return

        # =====================================================
        # 2. USUARIO PARA SENT_BY
        # =====================================================

        user = User.objects.first()

        if not user:

            self.stdout.write(
                self.style.ERROR(
                    "No existe ningún usuario en el sistema."
                )
            )

            return

        # =====================================================
        # CONTADORES
        # =====================================================

        migrated = 0
        errors = 0
        not_found = 0
        total_historicos = 0

        # =====================================================
        # 3. PROCESAR CADA PROCEDURE
        # =====================================================

        for index, procedure in enumerate(
            procedures,
            start=1
        ):

            try:

                # =================================================
                # DATOS DEL PROCEDURE
                # =================================================

                normalized_code = normalize_code(
                    procedure.code
                )

                tramite_type = (
                    procedure.tramite_type
                )

                self.stdout.write("")

                self.stdout.write(
                    self.style.WARNING(
                        f"[{index}/{total_procedures}] "
                        f"Procesando: "
                        f"{normalized_code} "
                        f"({tramite_type})"
                    )
                )

                # =================================================
                # BUSCAR HISTÓRICOS DE ESTE PROCEDURE
                #
                # IMPORTANTE:
                # código + tipo de trámite
                #
                # Así:
                #
                # 00001-2024 + TE
                #
                # no se mezcla con:
                #
                # 00001-2024 + TI
                # =================================================

                query = """
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

                    WHERE t.codigo = %s
                      AND h.tipo_tramite = %s

                    ORDER BY
                        h.secuencia
                """

                with connections["legacy"].cursor() as cursor:

                    cursor.execute(
                        query,
                        [
                            normalized_code,
                            tramite_type,
                        ]
                    )

                    columns = [
                        col[0]
                        for col in cursor.description
                    ]

                    rows = cursor.fetchall()

                # =================================================
                # SIN HISTÓRICOS
                # =================================================

                if not rows:

                    self.stdout.write(
                        self.style.WARNING(
                            f"  Sin históricos: "
                            f"{normalized_code} "
                            f"({tramite_type})"
                        )
                    )

                    not_found += 1

                    continue

                # =================================================
                # CANTIDAD DE HISTÓRICOS
                # =================================================

                total_historicos += len(rows)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Históricos encontrados: "
                        f"{len(rows)}"
                    )
                )

                # =================================================
                # 4. PROCESAR CADA HISTÓRICO
                # =================================================

                for row in rows:

                    data = dict(
                        zip(
                            columns,
                            row
                        )
                    )

                    # =============================================
                    # CÓDIGO DEL HISTÓRICO
                    # =============================================

                    raw_code = data.get(
                        "codigo"
                    )

                    historical_code = (
                        normalize_code(
                            raw_code
                        )
                    )

                    if not historical_code:

                        self.stdout.write(
                            self.style.ERROR(
                                "  Histórico sin código."
                            )
                        )

                        errors += 1

                        continue

                    # =============================================
                    # TIPO DEL HISTÓRICO
                    # =============================================

                    historical_type = data.get(
                        "tipo_tramite"
                    )

                    if historical_type not in [
                        "TE",
                        "TI"
                    ]:

                        self.stdout.write(
                            self.style.WARNING(
                                f"  Tipo ignorado: "
                                f"{historical_type}"
                            )
                        )

                        continue

                    # =============================================
                    # SEGURIDAD:
                    # VERIFICAR CODE + TIPO
                    # =============================================

                    if (
                        historical_code
                        != normalized_code
                        or historical_type
                        != tramite_type
                    ):

                        self.stdout.write(
                            self.style.ERROR(
                                f"  Histórico no coincide: "
                                f"{historical_code} "
                                f"({historical_type})"
                            )
                        )

                        errors += 1

                        continue

                    # =============================================
                    # SECUENCIA
                    # =============================================

                    secuencia = data.get(
                        "secuencia"
                    )

                    if secuencia is None:

                        self.stdout.write(
                            self.style.ERROR(
                                "  Histórico sin secuencia."
                            )
                        )

                        errors += 1

                        continue

                    # =============================================
                    # ÁREA ORIGEN DEL HISTÓRICO
                    # =============================================

                    origen_initials = data.get(
                        "origen_initials"
                    )

                    from_area = None

                    if origen_initials:

                        from_area = (
                            Area.objects
                            .filter(
                                initials__iexact=str(
                                    origen_initials
                                ).strip()
                            )
                            .first()
                        )

                    # =============================================
                    # ÁREA DESTINO
                    # =============================================

                    destino_initials = data.get(
                        "destino_initials"
                    )

                    to_area = None

                    if destino_initials:

                        to_area = (
                            Area.objects
                            .filter(
                                initials__iexact=str(
                                    destino_initials
                                ).strip()
                            )
                            .first()
                        )

                    # =============================================
                    # VALIDAR DESTINO
                    # =============================================

                    if not to_area:

                        self.stdout.write(
                            self.style.ERROR(
                                f"  Área destino no encontrada: "
                                f"{normalized_code} "
                                f"Secuencia: {secuencia} "
                                f"Destino: "
                                f"{destino_initials}"
                            )
                        )

                        errors += 1

                        continue

                    # =============================================
                    # ÁREA ORIGEN FINAL
                    #
                    # SECUENCIA 1:
                    #    toma el origen del Procedure
                    #
                    # RESTO:
                    #    toma el origen del histórico
                    # =============================================

                    if secuencia == 1 or secuencia == 2:

                        from_area_final = (
                            procedure.from_area
                        )

                    else:

                        from_area_final = (
                            from_area
                        )

                    # =============================================
                    # SUBJECT
                    # =============================================

                    origen_asunto = data.get(
                        "origen_asunto"
                    )

                    procedure_subject = (
                        procedure.subject
                    )

                    subject_derivar = None

                    if (
                        origen_asunto
                        and procedure_subject
                        and str(
                            origen_asunto
                        ).strip()
                        != str(
                            procedure_subject
                        ).strip()
                    ):

                        subject_derivar = (
                            origen_asunto
                        )

                    # =============================================
                    # ESTADO DEL TRÁMITE
                    # =============================================

                    estado_tramite = data.get(
                        "estado_tramite"
                    )

                    # =============================================
                    # IS DERIVE
                    # =============================================

                    is_derive = (
                        secuencia > 3
                        or (
                            secuencia == 3
                            and estado_tramite
                            not in FINAL_STATES
                        )
                    )

                    # =============================================
                    # ORIGIN OPTIONS
                    # =============================================

                    origin_options = (
                        parse_origin_options(
                            data.get(
                                "destino_asunto"
                            )
                        )
                    )

                    # =============================================
                    # COMENTARIO / OBSERVACIÓN
                    # =============================================

                    if estado_tramite == "Observado":

                        comment = data.get(
                            "observacion"
                        )

                    else:

                        comment = data.get(
                            "comentario"
                        )

                    # =============================================
                    # STATUS
                    # =============================================

                    status = STATUS_MAP.get(
                        estado_tramite,
                        ProcedureFlow.SENT
                    )

                    # =============================================
                    # IS ACTIVE
                    # =============================================

                    is_active = (
                        data.get("estado") == "V"
                    )

                    # =============================================
                    # IS TO FINALIZE
                    # =============================================

                    is_to_finalize = (
                        estado_tramite
                        == "Por finalizar"
                        or data.get("operacion")
                        == "PF"
                    )

                    # =============================================
                    # CREAR PROCEDURE FLOW
                    # =============================================

                    flow = ProcedureFlow.objects.create(

                        procedure=procedure,

                        from_area=from_area_final,

                        to_area=to_area,

                        flow_type=(
                            ProcedureFlow.NORMAL
                        ),

                        status=status,

                        subject=(
                            procedure.subject
                        ),

                        subject_derivar=(
                            subject_derivar
                        ),

                        comment=comment,

                        sent_by=user,

                        sequence=secuencia,

                        origin_options=(
                            origin_options
                        ),

                        is_active=is_active,

                        is_to_finalize=(
                            is_to_finalize
                        ),

                        is_derive=(
                            is_derive
                        ),
                    )

                    # =============================================
                    # FECHA DEL HISTÓRICO
                    # =============================================

                    created_at = data.get(
                        "created_at"
                    )

                    if created_at:

                        if created_at.tzinfo is None:

                            created_at = (
                                make_aware(
                                    created_at
                                )
                            )

                        flow.created_at = (
                            created_at
                        )

                        flow.save(
                            update_fields=[
                                "created_at"
                            ]
                        )

                    # =============================================
                    # CONTADOR
                    # =============================================

                    migrated += 1

                    # =============================================
                    # INFORMACIÓN
                    # =============================================

                    from_name = (
                        from_area_final.name
                        if from_area_final
                        else "SIN ORIGEN"
                    )

                    to_name = (
                        to_area.name
                        if to_area
                        else "SIN DESTINO"
                    )

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Flow creado | "
                            f"Secuencia: {secuencia} | "
                            f"Origen: {from_name} | "
                            f"Destino: {to_name}"
                        )
                    )

            except Exception as e:

                errors += 1

                self.stdout.write(
                    self.style.ERROR(
                        f"  ERROR en "
                        f"{procedure.code} "
                        f"({procedure.tramite_type}): "
                        f"{str(e)}"
                    )
                )

        # =====================================================
        # RESUMEN
        # =====================================================

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "=============================================="
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "RESUMEN DE REMIGRACIÓN"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "=============================================="
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Procedures procesados: "
                f"{total_procedures}"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Históricos encontrados: "
                f"{total_historicos}"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Flows creados: "
                f"{migrated}"
            )
        )

        self.stdout.write(
            self.style.WARNING(
                f"Procedures sin histórico: "
                f"{not_found}"
            )
        )

        self.stdout.write(
            self.style.ERROR(
                f"Errores: "
                f"{errors}"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Proceso finalizado."
            )
        )
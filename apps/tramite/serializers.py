from rest_framework import serializers
from django.utils.timezone import now
from django.conf import settings
from django.db import transaction
from apps.user.models import User
from .utils import send_procedure_rejected_email

from .models import ( 

    Company, Area, UserArea, Document, Agency, Procedure, WorkSchedule, Holiday,
    ProcedureFlow, Department, Province, District, GlobalBackup,
    ProcedureFile, SystemBackup,
    ProcedureSequence

)

from .utils import calculate_due_date, generate_procedure_code, generar_numeracion, get_next_sequence, get_virtual_areas, resolve_sequence_agency, check_schedule, ScheduleResult, generate_unique_tracking_code
    

import os
import logging

logger = logging.getLogger(__name__)

class GlobalBackupSerializer(serializers.ModelSerializer):

    class Meta:
        
        model = GlobalBackup
        fields = "__all__"

class SystemBackupSerializer(serializers.ModelSerializer):

    filename = serializers.ReadOnlyField()
    size_mb = serializers.ReadOnlyField()

    class Meta:
        model = SystemBackup
        fields = (
            "id",
            "filename",
            "backup_type",
            "size",
            "size_mb",
            "created_at"
        )

class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "description"]

class ProvinceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Province
        fields = ["id", "description", "department"]

class DistrictSerializer(serializers.ModelSerializer):
    class Meta:
        model = District
        fields = ["id", "description", "province"]

class CompanySerializer(serializers.ModelSerializer):

    class Meta:
        model = Company
        fields = '__all__'

    def update(self, instance, validated_data):
        # Verificar si hay un nuevo logo
        new_logo = validated_data.get("logo", None)
        if new_logo and instance.logo:
            # Eliminar el logo anterior del sistema de archivos
            old_logo_path = os.path.join(settings.MEDIA_ROOT, str(instance.logo))
            if os.path.exists(old_logo_path):
                os.remove(old_logo_path)

        instance.logo = new_logo if new_logo else instance.logo  # Mantener el anterior si no se envía nuevo
        instance.name = validated_data.get("name", instance.name)
        instance.ruc = validated_data.get("ruc", instance.ruc)
        instance.address = validated_data.get("address", instance.address)

        instance.save()
        return instance

class AgencySerializer(serializers.ModelSerializer):
    
    class Meta:

        model = Agency
        fields = '__all__'

class AreaSerializer(serializers.ModelSerializer):
    
    agency_name = serializers.CharField(source='agency.name', read_only=True)

    class Meta:

        model = Area
        fields = '__all__'

class MyAreaSerializer(serializers.ModelSerializer):

    area = AreaSerializer()

    class Meta:

        model = UserArea
        fields = '__all__'

class UserAreaSerializer(serializers.ModelSerializer):

    id = serializers.IntegerField(read_only=True)

    # 👉 PARA ESCRIBIR
    area_id = serializers.PrimaryKeyRelatedField(
        source='area',
        queryset=Area.objects.all(),
        write_only=True
    )

    # 👉 PARA LEER
    area = serializers.PrimaryKeyRelatedField(
        read_only=True
    )

    area_name = serializers.CharField(
        source='area.name',
        read_only=True
    )

    area_type = serializers.CharField(
        source='area.type',
        read_only=True
    )

    class Meta:
        model = UserArea
        fields = [
            'id',
            'area',       # lectura (id del área)
            'area_id',    # escritura
            'area_name',
            'area_type',
        ]

class ProcedureCodePreviewSerializer(serializers.Serializer):

    code = serializers.CharField()
    numeracion = serializers.CharField()

class DocumentSerializer(serializers.ModelSerializer):
    
    class Meta:

        model = Document
        fields = '__all__'

class WorkScheduleListSerializer(serializers.ListSerializer):
    def validate(self, data):
        days = [item["day"] for item in data]
        if len(days) != len(set(days)):
            raise serializers.ValidationError(
                "No se puede repetir el mismo día en el horario"
            )
        return data

class WorkScheduleSerializer(serializers.ModelSerializer):

    class Meta:
        model = WorkSchedule
        fields = "__all__"
        list_serializer_class = WorkScheduleListSerializer

        # 🔥 DESACTIVAR UniqueValidator AUTOMÁTICO
        extra_kwargs = {
            "day": {
                "validators": []
            }
        }

    def validate(self, data):
        start = data.get("start_time")
        end = data.get("end_time")

        if start and end and start >= end:
            raise serializers.ValidationError(
                "La hora de inicio debe ser menor que la hora de fin"
            )

        return data

class HolidaySerializer(serializers.ModelSerializer):
    
    class Meta:

        model = Holiday
        fields = '__all__'

# PROCEDURE

class ProcedureCreateSerializer(serializers.Serializer):

    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        required=False,
        allow_null=True
    )
    province = serializers.PrimaryKeyRelatedField(
        queryset=Province.objects.all(),
        required=False,
        allow_null=True
    )
    district = serializers.PrimaryKeyRelatedField(
        queryset=District.objects.all(),
        required=False,
        allow_null=True
    )

    # Documento
    document_type = serializers.PrimaryKeyRelatedField(
        queryset=Document.objects.all()
    )
    document_number = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True
    )
    subject = serializers.CharField()
    folios = serializers.IntegerField(
    min_value=0
    )

    # Remitente
    sender_dni = serializers.CharField(required=False, allow_blank=True)
    sender_name = serializers.CharField()
    sender_representante = serializers.CharField(required=False, allow_blank=True)
    sender_address = serializers.CharField(required=False, allow_blank=True)
    sender_phone = serializers.CharField(required=False, allow_blank=True)
    sender_email = serializers.EmailField(required=False, allow_blank=True)

    from_area = serializers.PrimaryKeyRelatedField(
        queryset=Area.objects.all(),
        required=False
    )
    # Destino
    agency = serializers.PrimaryKeyRelatedField(
        queryset=Agency.objects.all(),
        required=False
    )
    
    destination_area = serializers.PrimaryKeyRelatedField(
        queryset=Area.objects.all(),
        required=False,
        allow_null=True
    )

    copy_areas = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(
            queryset=Area.objects.all()
        ),
        required=False,
        allow_empty=True
    )

    # Flags
    is_virtual = serializers.BooleanField(default=False)

    def validate(self, data):

        request = self.context.get("request")
        is_virtual = data.get("is_virtual", False)

        if is_virtual:
            files = request.FILES.getlist("files")

            if not files:
                raise serializers.ValidationError({
                    "error": "Debe adjuntar al menos un archivo cuando el trámite es virtual."
                })

        return data
    
    @transaction.atomic
    def create(self, validated_data):

        try:

            request = self.context["request"]

            is_virtual = validated_data.get(
                "is_virtual",
                False
            )

            files = request.FILES.getlist("files")

            schedule_status = check_schedule(now())

            if schedule_status == ScheduleResult.NO_LABORABLE:
                raise serializers.ValidationError({
                    "error": "No disponible."
                })

            flow_status = ProcedureFlow.SENT
            registered_out_of_schedule_at = None

            if schedule_status == ScheduleResult.OUT_OF_SCHEDULE:
                flow_status = ProcedureFlow.PENDING_SCHEDULE
                registered_out_of_schedule_at = now()

            tracking_code = None

            if is_virtual:

                agency = validated_data.pop(
                    "agency",
                    None
                )

                tracking_code = generate_unique_tracking_code()

                user = User.objects.first()

                from_area, to_area = get_virtual_areas(
                    agency
                )

                destination_areas = [to_area]

            else:

                user = request.user

                from_area = validated_data.pop(
                    "from_area",
                    None
                )

                destination_area = validated_data.pop(
                    "destination_area",
                    None
                )

                destination_areas = (
                    [destination_area]
                    if destination_area else []
                )

            copy_areas = validated_data.pop(
                "copy_areas",
                []
            )

            created = []


            for area in destination_areas:

                origin_agency = ( area.agency if is_virtual else from_area.agency )

                # destination_agency = area.agency

                now_date = now()

                # =====================================
                # 1. CREAR TRÁMITE TEMPORAL
                # =====================================

                procedure = Procedure.objects.create(
                    agency=origin_agency,
                    created_by=user,
                    from_area=from_area,
                    tramite_type=from_area.type,
                    to_area=area,
                    tracking_code=tracking_code,
                    due_date=calculate_due_date(now_date),
                    code=None,
                    is_registered=False,
                    numeracion=generar_numeracion(from_area),
                    **validated_data
                )

                # =====================================
                # 2. CREAR FLOW
                # =====================================

                ProcedureFlow.objects.create(
                    procedure=procedure,
                    to_area=area,
                    sent_by=user,
                    sequence=1,
                    subject=procedure.subject,
                    from_area=procedure.from_area,
                    flow_type=ProcedureFlow.NORMAL,
                    status=flow_status,
                    is_active=True,
                    registered_out_of_schedule_at=registered_out_of_schedule_at
                )

                # =====================================
                # 3. GUARDAR ARCHIVOS
                # =====================================

                saved_files = []

                for file in files:

                    obj = ProcedureFile.objects.create(
                        procedure=procedure,
                        file=file,
                        uploaded_by=user,
                        original_name=file.name
                    )

                    saved_files.append(obj)

                # =====================================
                # 4. VALIDAR ARCHIVOS FÍSICOS
                # =====================================

                for saved_file in saved_files:

                    if not saved_file.file:
                        raise Exception(
                            "Archivo inválido."
                        )

                    if not os.path.exists(
                        saved_file.file.path
                    ):
                        raise Exception(
                            f"Archivo no encontrado: "
                            f"{saved_file.file.path}"
                        )



                # =====================================
                # 5. GENERAR CORRELATIVO
                # =====================================

                procedure.code = generate_procedure_code(from_area)

                # =====================================
                # 5. GENERAR ERROR DE PRUEBA
                # =====================================

                # raise Exception("ERROR DE PRUEBA")

                # =====================================
                # 6. GENERAR CÓDIGO DESTINO
                # =====================================

                # if (
                #     not is_virtual and
                #     destination_agency.id == main_agency.id and
                #     origin_agency.id != main_agency.id
                # ):

                #     procedure.code_destino = (
                #         generate_procedure_code(
                #             main_agency
                #         )
                #     )

                # =====================================
                # 7. CONFIRMAR REGISTRO
                # =====================================

                procedure.is_registered = True

                procedure.save(
                    update_fields=[
                        "code",
                        "code_destino",
                        "is_registered"
                    ]
                )

                # =====================================
                # 8. COPIAS
                # =====================================

                for copy_area in copy_areas:

                    ProcedureFlow.objects.create(
                        procedure=procedure,
                        to_area=copy_area,
                        sent_by=user,
                        sequence=1,
                        subject=procedure.subject,
                        from_area=procedure.from_area,
                        flow_type=ProcedureFlow.COPY,
                        status=flow_status,
                        is_active=True,
                        registered_out_of_schedule_at=registered_out_of_schedule_at
                    )

                created.append(procedure)

            return created

        except Exception:

            logger.exception(
                "Error registrando trámite"
            )

            raise

class ProcedureUpdateSerializer(serializers.ModelSerializer):

    class Meta:
        model = Procedure
        fields = [
            "document_type",
            "document_number",
            "folios",
            "subject",

            "sender_dni",
            "sender_name",
            "sender_address",
            "sender_phone",
            "sender_email",

            # ✅ UBICACIÓN
            "department",
            "province",
            "district",

            "from_area",
            "to_area",
            "is_virtual",
        ]

    def validate(self, data):

        procedure: Procedure = self.context["procedure"]

        flows_qs = ProcedureFlow.objects.filter(procedure=procedure, flow_type=ProcedureFlow.NORMAL)

        # ❌ No editable si tiene más de 1 flujo
        if flows_qs.count() > 1:
            raise serializers.ValidationError(
                "Este trámite no se puede editar porque ya tiene más de un flujo"
            )

        return data

    @transaction.atomic
    def update(self, instance, validated_data):

        """
        - Actualiza Procedure
        - Si existe 1 flow, sincroniza subject y to_area
        """

        # 1️⃣ Actualizar Procedure
        procedure = super().update(instance, validated_data)

        # 2️⃣ Obtener el único flujo (si existe)
        flow = (
            ProcedureFlow.objects
            .filter(procedure=procedure)
            .order_by("created_at")
            .first()
        )

        if flow:
            update_fields = []

            if "subject" in validated_data:
                flow.subject = procedure.subject
                update_fields.append("subject")

            if "to_area" in validated_data:
                flow.to_area = procedure.to_area
                update_fields.append("to_area")

            if update_fields:
                flow.save(update_fields=update_fields)

        return procedure

class ProcedureFileSerializer(serializers.ModelSerializer):

    file_name = serializers.CharField(source="filename", read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = ProcedureFile
        fields = ("id", "file_name", "file_url", "created_at")

    def get_file_url(self, obj):
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.file.url)
        return obj.file.url

class ProcedureCopySerializer(serializers.ModelSerializer):

    area = AreaSerializer(source="to_area")

    class Meta:
        model = ProcedureFlow
        fields = (
            "id",
            "area",
            "status",
            "created_at",
        )

class ProcedureListSerializer(serializers.ModelSerializer):

    files = ProcedureFileSerializer(many=True, read_only=True)
    department = DepartmentSerializer()
    province = ProvinceSerializer()
    district = DistrictSerializer()
    from_area = AreaSerializer()
    to_area = AreaSerializer()
    document_type = DocumentSerializer()
    agency = AgencySerializer()
    copies = serializers.SerializerMethodField()  # 👈 CLAVE
    is_rejected = serializers.SerializerMethodField()  # 👈 NUEVO
    reject_comment = serializers.SerializerMethodField()  

    class Meta:
        model = Procedure
        fields = '__all__'

    def get_copies(self, obj):
        copies = (
            ProcedureFlow.objects
            .filter(
                procedure=obj,
                flow_type=ProcedureFlow.COPY
            )
            .select_related("to_area")
            .order_by("sequence")
        )
        return ProcedureCopySerializer(copies, many=True).data

    def get_is_rejected(self, obj):
        return obj.flows.filter(
            status=ProcedureFlow.REJECTED,
            is_active=True
        ).exists()

    def get_reject_comment(self, obj):
        flow = obj.flows.filter(
            status=ProcedureFlow.REJECTED,
            is_active=True
        ).first()  # por seguridad

        return flow.comment if flow else None

class ProcedureAnnulSerializer(serializers.Serializer):

    comment = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):

        procedure: Procedure = self.context["procedure"]

        flows = ProcedureFlow.objects.filter(procedure=procedure)

        if flows.count() > 1:
            raise serializers.ValidationError(
                "Este trámite no se puede editar porque ya tiene más de un flujo"
            )

        return data

    @transaction.atomic
    def save(self):

        procedure: Procedure = self.context["procedure"]
        comment = self.validated_data.get("comment", "")

        # 1️⃣ Marcar trámite como anulado
        procedure.is_annulled = True

        procedure.save(
            update_fields=["is_annulled"]
        )

        # 2️⃣ Actualizar flujo
        flow = ProcedureFlow.objects.get(procedure=procedure)

        flow.status = ProcedureFlow.ANNULLED
        flow.comment = comment
        flow.is_active = False
        flow.save(
            update_fields=["status", "comment", "is_active"]
        )

        return procedure

class ProcedureUpdateCopiesSerializer(serializers.Serializer):

    copy_areas = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(
            queryset=Area.objects.all()
        ),
        allow_empty=True
    )

# PROCEDURE FLOW

class ProcedureFlowSerializer(serializers.ModelSerializer):

    procedure = ProcedureListSerializer()
    from_area = AreaSerializer()
    to_area = AreaSerializer()
    is_copy = serializers.SerializerMethodField()
    original_finalizado = serializers.BooleanField(read_only=True)

    class Meta:

        model = ProcedureFlow
        fields = '__all__'

    def get_is_copy(self, obj):

        return obj.flow_type == ProcedureFlow.COPY

# RECEPCIONAR
class ReceiveFlowSerializer(serializers.Serializer):

    def validate(self, data):

        flow: ProcedureFlow = self.context["flow"]
        request = self.context["request"]
        procedure = flow.procedure

        # BLOQUEAR SI ESTÁ VENCIDO
        if procedure.is_expired:
            raise serializers.ValidationError(
                "Este trámite está bloqueado por fuera del plazo de finalización"
            )

        # Debe estar enviado
        if flow.status != ProcedureFlow.SENT:
            raise serializers.ValidationError("The procedure is not pending reception")

        # Área correcta
        area_id = request.headers.get("X-Area-Id")
        if str(flow.to_area_id) != str(area_id):
            raise serializers.ValidationError("You cannot receive a procedure from another area")

        return data

    @transaction.atomic
    def save(self):

        flow: ProcedureFlow = self.context["flow"]
        user = self.context["request"].user

        procedure = flow.procedure

        # 🔒 Desactivar flow NORMAL activo
        flow.is_active = False
        flow.save(update_fields=["is_active"])

        # ➕ Crear nuevo flow RECEIVED
        new_flow = ProcedureFlow.objects.create(
            procedure=procedure,
            sequence=get_next_sequence(procedure),
            flow_type=flow.flow_type,
            status=ProcedureFlow.RECEIVED,
            to_area=flow.to_area,
            sent_by=user,
            is_active=True,
            subject=flow.subject,
            subject_derivar=flow.subject_derivar,
            from_area=flow.from_area,
            is_to_finalize=flow.is_to_finalize,
            origin_options=flow.origin_options,
            is_to_observed=flow.is_to_observed,
            is_derive=flow.is_derive,

            # 👇 NUEVO
            document_type=flow.document_type,
            document_number=flow.document_number,
            folios=flow.folios,

        )

        return new_flow

# DERVIVAR
class DeriveFlowSerializer(serializers.Serializer):
    
    document_type = serializers.PrimaryKeyRelatedField(
        queryset=Document.objects.all(),
        required=False,
        allow_null=True
    )

    document_number = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True
    )

    folios = serializers.IntegerField(
        required=False
    )

    origin_options = serializers.JSONField(required=False)

    destination_area = serializers.PrimaryKeyRelatedField(
        queryset=Area.objects.all(),
        required=False,
        allow_null=True
    )
    
    copy_areas = serializers.ListField(
        child=serializers.PrimaryKeyRelatedField(queryset=Area.objects.all()),
        required=False
    )
    subject_derivar = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):

        flow: ProcedureFlow = self.context["flow"]
        request = self.context["request"]

        procedure = flow.procedure

        # BLOQUEAR SI ESTÁ VENCIDO
        if procedure.is_expired:
            raise serializers.ValidationError(
                "Este trámite está bloqueado por fuera del plazo de finalización"
            )

        # Debe estar recepcionado
        if flow.status != ProcedureFlow.RECEIVED:
            raise serializers.ValidationError("The procedure must be received before deriving")

        # Área correcta
        area_id = request.headers.get("X-Area-Id")
        if str(flow.to_area_id) != str(area_id):
            raise serializers.ValidationError("You cannot derive from another area")

        return data

    @transaction.atomic
    def save(self):

        request = self.context["request"]
        flow: ProcedureFlow = self.context["flow"]
        user = request.user
        procedure = flow.procedure

        destination_area = self.validated_data.pop("destination_area", None)
        copy_areas = self.validated_data.get("copy_areas", [])
        subject_derivar = self.validated_data.get("subject_derivar", "")
        origin_options = self.validated_data.get("origin_options", [])

        # 👇 NUEVOS DATOS DOCUMENTO
        new_document_type = self.validated_data.get(
            "document_type",
            procedure.document_type
        )

        new_document_number = self.validated_data.get(
            "document_number",
            procedure.document_number
        )

        new_folios = self.validated_data.get(
            "folios",
            procedure.folios
        )

        files = request.FILES.getlist("files")

        # 🔒 Desactivar NORMAL activo previo
        flow.is_active = False
        flow.save(update_fields=["is_active"])

        created = []

        first_sequence = get_next_sequence(procedure)

        destination_areas = [destination_area] if destination_area else []

        # ➡️ Crear flows NORMAL
        for area in destination_areas:

            created.append(
                ProcedureFlow.objects.create(
                    procedure=procedure,
                    sequence=get_next_sequence(procedure),
                    flow_type=ProcedureFlow.NORMAL,
                    status=ProcedureFlow.SENT,
                    from_area=flow.to_area,
                    to_area=area,
                    sent_by=user,
                    subject=flow.subject,
                    subject_derivar=subject_derivar,
                    is_active=True,
                    origin_options=origin_options,
                    is_derive=True,

                    # 👇 SOLO SI CAMBIÓ
                    document_type=new_document_type,
                    document_number=new_document_number,
                    folios=new_folios,
                )
            )

        # 📎 Archivos
        for file in files:
            ProcedureFile.objects.create(
                procedure=procedure,
                file=file,
                uploaded_by=user,
                original_name=file.name
            )

        # 📋 FLOWS COPY
        for area in copy_areas:

            created.append(
                ProcedureFlow.objects.create(
                    procedure=procedure,
                    sequence=first_sequence,
                    flow_type=ProcedureFlow.COPY,
                    status=ProcedureFlow.SENT,
                    from_area=flow.to_area,
                    to_area=area,
                    sent_by=user,
                    subject=subject_derivar,
                    subject_derivar=subject_derivar,
                    origin_options=origin_options,
                    is_derive=True,

                    # 👇 TAMBIÉN EN COPIAS
                    document_type=new_document_type,
                    document_number=new_document_number,
                    folios=new_folios,
                )
            )

        return created
    
# FINALIZAR
class FinalizeFlowSerializer(serializers.Serializer):

    comment = serializers.CharField(
            required=False,
            allow_blank=True,
            max_length=500
    )

    def validate_comment(self, value):

        if not value:
            return value

        lines = value.splitlines()

        if len(lines) > 4:
            raise serializers.ValidationError(
                "Solo se permiten máximo 4 líneas"
            )

        return value

    def validate(self, data):


        flow: ProcedureFlow = self.context["flow"]
        request = self.context["request"]

        procedure = flow.procedure

        # BLOQUEAR SI ESTÁ VENCIDO
        if procedure.is_expired:
            raise serializers.ValidationError(
                "Este trámite está bloqueado por fuera del plazo de finalización"
            )

        if flow.status != ProcedureFlow.RECEIVED:
            raise serializers.ValidationError("Only received procedures can be finalized")

        area_id = request.headers.get("X-Area-Id")
        if str(flow.to_area_id) != str(area_id):
            raise serializers.ValidationError("You cannot finalize from another area")

        return data

    def save(self):

        flow: ProcedureFlow = self.context["flow"]
        user = self.context["request"].user
        procedure = flow.procedure
        comment = self.validated_data.get("comment")

        #  Desactivar NORMAL activo
        flow.is_active = False
        flow.save(update_fields=["is_active"])

        #  Crear flow FINALIZED
        new_flow = ProcedureFlow.objects.create(
            procedure=procedure,
            sequence=get_next_sequence(procedure),
            flow_type=flow.flow_type,
            status=ProcedureFlow.FINALIZED,
            from_area=flow.from_area,
            to_area=flow.to_area,
            sent_by=user,
            is_active=True,
            subject=flow.subject,
            subject_derivar=flow.subject_derivar,
            is_derive = flow.is_derive,

            finalize_comment = comment if comment else None,

            # 👇 NUEVO
            document_type=flow.document_type,
            document_number=flow.document_number,
            folios=flow.folios,

        )

        ProcedureFlow.objects.filter(
            procedure=procedure,
            flow_type=ProcedureFlow.COPY,
            status=ProcedureFlow.RECEIVED,
            is_active=True
        ).update(status=ProcedureFlow.FINALIZED)

        return new_flow

# RECHAZAR 
class RejectFlowSerializer(serializers.Serializer):

    comment = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):

        flow: ProcedureFlow = self.context["flow"]
        request = self.context["request"]

        procedure = flow.procedure

        # BLOQUEAR SI ESTÁ VENCIDO
        if procedure.is_expired:
            raise serializers.ValidationError(
                "Este trámite está bloqueado por fuera del plazo de finalización"
            )

        # Solo se rechaza una rama SENT activa
        if flow.status != ProcedureFlow.SENT or not flow.is_active:
            raise serializers.ValidationError("Only active sent procedures can be rejected")

        # Validar área activa
        area_id = request.headers.get("X-Area-Id")
        if str(flow.to_area_id) != str(area_id):
            raise serializers.ValidationError("You cannot reject from another area")

        return data

    def save(self):

        flow: ProcedureFlow = self.context["flow"]
        user = self.context["request"].user
        procedure = flow.procedure

        # 🔒Cerrar SOLO esta rama (NO todas)
        flow.is_active = False
        flow.save(update_fields=["is_active"])

        #  Crear flow REJECTED (evento)
        rejected_flow = ProcedureFlow.objects.create(
            procedure=procedure,
            sequence=get_next_sequence(procedure),
            flow_type=flow.flow_type,
            status=ProcedureFlow.REJECTED,
            from_area=flow.to_area,   # 👈 quien rechaza
            to_area=flow.to_area,     # 👈 quien ejecuta la acción
            comment=self.validated_data.get("comment", ""),
            is_active=True,        
            subject=flow.subject,
            subject_derivar=flow.subject_derivar,
            sent_by=user,
            is_to_finalize=flow.is_to_finalize,
            origin_options=flow.origin_options,
            is_derive = flow.is_derive,

            # 👇 NUEVO
            document_type=flow.document_type,
            document_number=flow.document_number,
            folios=flow.folios,
        )

        return rejected_flow

# OBSERVAR 
class ObservedFlowSerializer(serializers.Serializer):

    comment = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):

        flow: ProcedureFlow = self.context["flow"]
        request = self.context["request"]

        procedure = flow.procedure

        # BLOQUEAR SI ESTÁ VENCIDO
        if procedure.is_expired:
            raise serializers.ValidationError(
                "Este trámite está bloqueado por fuera del plazo de finalización"
            )

        if flow.status != ProcedureFlow.RECEIVED:
            raise serializers.ValidationError("Only sent procedures can be observed")

        area_id = request.headers.get("X-Area-Id")
        if str(flow.to_area_id) != str(area_id):
            raise serializers.ValidationError("You cannot operate from another area")

        return data

    def save(self):

        flow: ProcedureFlow = self.context["flow"]
        user = self.context["request"].user
        procedure = flow.procedure

        # 🔒 Desactivar NORMAL activo
        flow.is_active = False
        flow.save(update_fields=["is_active"])

        # ⚠️ Crear flow OBSERVED o REJECTED
        new_flow = ProcedureFlow.objects.create(
            procedure=procedure,
            sequence=get_next_sequence(procedure),
            flow_type=flow.flow_type,
            status=ProcedureFlow.OBSERVED,
            from_area=flow.from_area,   # 👈 quien rechaza
            to_area=flow.to_area,     # 👈 quien ejecuta la acción
            comment=self.validated_data.get("comment", ""),
            is_active=True,          # 👈 RECHAZO NUNCA ES ACTIVO
            subject=flow.subject,
            subject_derivar=flow.subject_derivar,
            sent_by=user,
            is_to_finalize=flow.is_to_finalize,
            origin_options=flow.origin_options,
            is_derive = flow.is_derive,

            # 👇 NUEVO
            document_type=flow.document_type,
            document_number=flow.document_number,
            folios=flow.folios,
               
        )

        return new_flow

#  REENVIAR
class ResendObservedFlowSerializer(serializers.Serializer):

    # Flujo
    destination_area = serializers.PrimaryKeyRelatedField(
        queryset=Area.objects.all()
    )
    subject = serializers.CharField(required=False, allow_blank=True)
    subject_derivar = serializers.CharField(required=False, allow_blank=True)


    # Corrección del expediente
    document_type = serializers.PrimaryKeyRelatedField(
        queryset=Document.objects.all(),
        required=False
    )

    document_number = serializers.CharField(required=False, allow_blank=True)
    folios = serializers.IntegerField(required=False, min_value=0)

    def validate(self, data):
        flow: ProcedureFlow = self.context["flow"]

        procedure = flow.procedure

        # BLOQUEAR SI ESTÁ VENCIDO
        if procedure.is_expired:
            raise serializers.ValidationError(
                "Este trámite está bloqueado por fuera del plazo de finalización"
            )


        if flow.status != ProcedureFlow.OBSERVED:
            raise serializers.ValidationError(
                "Only observed procedures can be resent"
            )

        return data

    def save(self):

        request = self.context["request"]
        flow: ProcedureFlow = self.context["flow"]
        user = request.user
        procedure = flow.procedure

        # 🔒 Cerrar flow observado
        flow.is_active = False
        flow.save(update_fields=["is_active"])

        if not flow.is_derive:

            # ACTUALIZAR PROCEDURE (SOLO CAMPOS PERMITIDOS)

            editable_fields = [
                "document_type",
                "document_number",
                "folios",
            ]

            updated_fields = []

            for field in editable_fields:
                if field in self.validated_data:
                    setattr(procedure, field, self.validated_data[field])
                    updated_fields.append(field)

            if updated_fields:
                procedure.save(update_fields=updated_fields)

            # 📎 Archivos de corrección (solo agregar)
            for file in request.FILES.getlist("files"):
                ProcedureFile.objects.create(
                    procedure=procedure,
                    file=file,
                    uploaded_by=user,
                    original_name=file.name
                )

        #  Crear nuevo flow SENT
        active_area_id = int(request.headers.get("X-Area-Id"))
        active_area = Area.objects.get(id=active_area_id)
        destination_area = self.validated_data["destination_area"]
        subject = self.validated_data.get("subject", None)
        subject_derivar = self.validated_data.get("subject_derivar", None)

        new_flow = ProcedureFlow.objects.create(
            procedure=procedure,
            sequence=get_next_sequence(procedure),
            flow_type=ProcedureFlow.NORMAL,
            status=ProcedureFlow.SENT,
            from_area=active_area,
            to_area=destination_area,
            comment=flow.comment,
            is_active=True,
            subject=subject,
            subject_derivar=subject_derivar,
            sent_by=user,
            is_to_finalize=flow.is_to_finalize,
            origin_options=flow.origin_options,
            is_to_observed=True,
            is_derive=flow.is_derive,

            # 👇 NUEVO
            document_type=flow.document_type,
            document_number=flow.document_number,
            folios=flow.folios,
        )

        return new_flow

# SUBSANAR
class SubsanarFlowSerializer(serializers.Serializer):

    comment = serializers.CharField(required=False, allow_blank=True)

    def save(self):

        flow = self.context["flow"]              # flow OBSERVED actual
        user = self.context["request"].user
        procedure = flow.procedure

      
        # BLOQUEAR SI ESTÁ VENCIDO
        if procedure.is_expired:
            raise serializers.ValidationError(
                "Este trámite está bloqueado por fuera del plazo de finalización"
            )


        # 🔒 Validación mínima
        if flow.status != ProcedureFlow.OBSERVED:
            raise serializers.ValidationError(
                "El trámite no se encuentra observado"
            )

        # 1️⃣ Obtener el primer flow (inicio)
        first_flow = (
            ProcedureFlow.objects
            .filter(procedure=procedure)
            .order_by("sequence")
            .first()
        )

        if not first_flow:
            raise serializers.ValidationError(
                "No se encontró el flujo inicial del trámite"
            )

        # 2️⃣ Eliminar todos los flows posteriores
        ProcedureFlow.objects.filter(
            procedure=procedure
        ).exclude(id=first_flow.id).delete()

        # 3️⃣ Actualizar el flow inicial
        first_flow.status = ProcedureFlow.OBSERVED
        first_flow.comment = self.validated_data.get("comment")
        first_flow.is_active = True
        first_flow.is_corrected = True
        first_flow.sequence = 1
        first_flow.save()

        return first_flow

##### CONSULTAR

class ProcedureFlowSeachSerializer(serializers.ModelSerializer):

    procedure = ProcedureListSerializer()
    from_area = AreaSerializer()
    to_area = AreaSerializer()
    is_copy = serializers.SerializerMethodField()
    original_finalizado = serializers.BooleanField(read_only=True)

    class Meta:

        model = ProcedureFlow
        fields = '__all__'

    def get_is_copy(self, obj):

        return obj.flow_type == ProcedureFlow.COPY

class ProcedureDetailSerializer(serializers.ModelSerializer):

    flows = ProcedureFlowSeachSerializer(many=True,read_only=True)

    files = ProcedureFileSerializer(many=True, read_only=True)
    department = DepartmentSerializer()
    province = ProvinceSerializer()
    district = DistrictSerializer()
    from_area = AreaSerializer()
    to_area = AreaSerializer()
    document_type = DocumentSerializer()
    agency = AgencySerializer()
    copies = serializers.SerializerMethodField()  # 👈 CLAVE
    is_rejected = serializers.SerializerMethodField()  # 👈 NUEVO
    reject_comment = serializers.SerializerMethodField()  

    class Meta:

        model = Procedure
        fields = '__all__'

    def get_copies(self, obj):
        copies = (
            ProcedureFlow.objects
            .filter(
                procedure=obj,
                flow_type=ProcedureFlow.COPY
            )
            .select_related("to_area")
            .order_by("sequence")
        )
        return ProcedureCopySerializer(copies, many=True).data

    def get_is_rejected(self, obj):
        return obj.flows.filter(
            status=ProcedureFlow.REJECTED,
            is_active=True
        ).exists()

    def get_reject_comment(self, obj):
        flow = obj.flows.filter(
            status=ProcedureFlow.REJECTED,
            is_active=True
        ).first()  # por seguridad

        return flow.comment if flow else None

# UPDATE FLOW

class AdminProcedureUpdateSerializer(serializers.ModelSerializer):

    class Meta:
        model = Procedure
        fields = [
            "document_type",
            "document_number",
            "folios",
            "subject",

            "sender_dni",
            "sender_name",
            "sender_address",
            "sender_phone",
            "sender_email",

            # ✅ UBICACIÓN
            "department",
            "province",
            "district",

            "from_area",
            "to_area",
            "is_virtual",
        ]

    # def validate(self, data):

    #     procedure: Procedure = self.context["procedure"]

    #     flows_qs = ProcedureFlow.objects.filter(procedure=procedure, flow_type=ProcedureFlow.NORMAL)

    #     # ❌ No editable si tiene más de 1 flujo
    #     if flows_qs.count() > 1:
    #         raise serializers.ValidationError(
    #             "Este trámite no se puede editar porque ya tiene más de un flujo"
    #         )

    #     return data

    @transaction.atomic
    def update(self, instance, validated_data):

        """
        - Actualiza Procedure
        - Si existe 1 flow, sincroniza subject y to_area
        """

        # 1️⃣ Actualizar Procedure
        procedure = super().update(instance, validated_data)

        # 2️⃣ Obtener el único flujo (si existe)
        # flow = (
        #     ProcedureFlow.objects
        #     .filter(procedure=procedure)
        #     .order_by("created_at")
        #     .first()
        # )

        # if flow:
        #     update_fields = []

        #     if "subject" in validated_data:
        #         flow.subject = procedure.subject
        #         update_fields.append("subject")

        #     if "to_area" in validated_data:
        #         flow.to_area = procedure.to_area
        #         update_fields.append("to_area")

        #     if update_fields:
        #         flow.save(update_fields=update_fields)

        return procedure


class ProcedureFlowUpdateSerializer(serializers.ModelSerializer):

    class Meta:
        model = ProcedureFlow
        fields = '__all__'

    def validate(self, data):

        flow: ProcedureFlow = self.instance

        # validar si tiene registros posteriores
        has_next_flows = ProcedureFlow.objects.filter(
            procedure=flow.procedure,
            sequence__gt=flow.sequence,
            is_active=True
        ).exists()

        if has_next_flows:
            raise serializers.ValidationError("No se puede editar porque existen movimientos posteriores.")

        return data
 


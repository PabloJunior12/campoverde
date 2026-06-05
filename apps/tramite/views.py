from .serializers import CompanySerializer, ProvinceSerializer, SubsanarFlowSerializer, ProcedureCodePreviewSerializer, SystemBackupSerializer, GlobalBackupSerializer,  DepartmentSerializer, ProcedureUpdateCopiesSerializer, DistrictSerializer, ProcedureAnnulSerializer,  WorkScheduleSerializer, HolidaySerializer, ProcedureUpdateSerializer, ResendObservedFlowSerializer, RejectFlowSerializer, ObservedFlowSerializer, AreaSerializer, FinalizeFlowSerializer, DeriveFlowSerializer, ProcedureFlowSerializer, ReceiveFlowSerializer, DocumentSerializer, ProcedureListSerializer, MyAreaSerializer, AgencySerializer, ProcedureCreateSerializer
from .models import Company, Department, Province, District, UserArea, Area, Document, Agency, Procedure, ProcedureFlow, GlobalBackup, ProcedureFile, Holiday, WorkSchedule, SystemBackup

from rest_framework import status, viewsets, generics
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action

from django.db.models import OuterRef, Subquery, Q, Exists
from django.template.loader import render_to_string
from django.http import HttpResponse, FileResponse, Http404
from django.db import transaction, models
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from django.shortcuts import render, get_object_or_404
from django.conf import settings
from django.core.management import call_command

from datetime import datetime
from weasyprint import HTML

import subprocess
import os

from .utils import generar_qr_base64, preview_procedure_code, ProcedureFilter, PendingFlowFilter, send_procedure_email, send_procedure_rejected_email, get_flow_status_display, get_flow_global_status_display, check_schedule, ScheduleResult


class CustomPagination(PageNumberPagination):

    page_size = 5  # Número de registros por página
    page_size_query_param = 'page_size'  # Permite cambiar el tamaño desde la URL
    max_page_size = 100  # Tamaño máximo permitido

class DepartmentListAPIView(generics.ListAPIView):
    
    queryset = Department.objects.filter(active=True).order_by("description")
    serializer_class = DepartmentSerializer
    authentication_classes = []
    permission_classes = []

class ProvinceListAPIView(generics.ListAPIView):

    serializer_class = ProvinceSerializer
    authentication_classes = []
    permission_classes = []

    def get_queryset(self):
        department_id = self.request.query_params.get("department")
        return Province.objects.filter(
            department_id=department_id,
            active=True
        ).order_by("description")

class DistrictListAPIView(generics.ListAPIView):

    serializer_class = DistrictSerializer
    authentication_classes = []
    permission_classes = []

    def get_queryset(self):
        province_id = self.request.query_params.get("province")
        return District.objects.filter(
            province_id=province_id,
            active=True
        ).order_by("description")

class HolidayViewSet(viewsets.ModelViewSet):

    queryset = Holiday.objects.all().order_by('date')
    serializer_class = HolidaySerializer

class WorkScheduleViewSet(viewsets.ModelViewSet):

    queryset = WorkSchedule.objects.all()
    serializer_class = WorkScheduleSerializer

    @action(detail=False, methods=["put"], url_path="bulk-update")
    @transaction.atomic
    def bulk_update(self, request):
        """
        Reemplaza completamente los horarios laborales
        """
        serializer = WorkScheduleSerializer(
            data=request.data,
            many=True
        )
        serializer.is_valid(raise_exception=True)

        WorkSchedule.objects.all().delete()

        WorkSchedule.objects.bulk_create([
            WorkSchedule(**item)
            for item in serializer.validated_data
        ])

        return Response(
            {"message": "Horario laboral actualizado correctamente"},
            status=status.HTTP_200_OK
        )

class CompanyViewSet(viewsets.ModelViewSet):

    authentication_classes = [] 
    permission_classes = []    
    queryset = Company.objects.all()
    serializer_class = CompanySerializer

class AreaViewSet(viewsets.ModelViewSet):

    queryset = Area.objects.filter(state = True).order_by('code')
    serializer_class = AreaSerializer

class AgencyViewSet(viewsets.ModelViewSet):

    authentication_classes = [] 
    permission_classes = []    
    queryset = Agency.objects.filter(state = True).order_by('id')
    serializer_class = AgencySerializer

class DocumentViewSet(viewsets.ModelViewSet):

    authentication_classes = [] 
    permission_classes = []    
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer

class MyAreasView(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request):

        user_areas = UserArea.objects.filter(
            user=request.user,
            # activo=True,
            area__state=True
        )

        serializer = MyAreaSerializer(user_areas, many=True)
        
        return Response(serializer.data)
    
# --------- PROCEDURE

class CheckScheduleAPIView(APIView):
    
    authentication_classes = []
    permission_classes = []

    def get(self, request):

        current_time = timezone.now()
        schedule_status = check_schedule(current_time)

        # ❌ Domingo o feriado
        if schedule_status == ScheduleResult.NO_LABORABLE:
            return Response(
                {
                    "error": (
                        "Estimado usuario. El trámite no puede ser registrado "
                        "porque la fecha corresponde a un día feriado o domingo. "
                        "Por favor, realice el registro en un día hábil."
                    )
                },
                status=status.HTTP_403_FORBIDDEN
            )

        # ❌ Fuera de horario laboral
        if schedule_status == ScheduleResult.OUT_OF_SCHEDULE:
            return Response(
                {
                    "error": (
                        "Estimado usuario. El registro de trámites solo está "
                        "disponible dentro del horario de atención."
                    )
                },
                status=status.HTTP_403_FORBIDDEN
            )

        return Response(
            {
                "message": "Registro habilitado"
            },
            status=status.HTTP_200_OK
        )

class ProcedureListVirtualesAPIView(generics.ListAPIView):

    serializer_class = ProcedureListSerializer
    pagination_class = CustomPagination

    def get_queryset(self):

        area = self.get_active_area()
        year = self.request.query_params.get("year")

        queryset = Procedure.objects.filter(
            to_area=area,
            is_virtual=True
        )

        # 👇 Solo aplicar filtro si year existe
        if year:
            queryset = queryset.filter(created_at__year=year)

        return (
            queryset
            .prefetch_related("files", "to_area")
            .order_by("-code")
        )

    def get_serializer_context(self):

        return {"request": self.request}
    
    def get_active_area(self):
        area_id = self.request.headers.get("X-Area-Id")
    
        if not area_id:
            return None
        return Area.objects.filter(id=area_id).first()

class ProcedureVirtualCreateAPIView(APIView):

    authentication_classes = []
    permission_classes = []

    def post(self, request):

        # 🔒 VALIDAR HORARIO
        schedule_status = check_schedule(timezone.now())

        if schedule_status == ScheduleResult.NO_LABORABLE:
            return Response(
                {
                    "error": "Mesa de partes virtual no recibe documentos los domingos ni feriados."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        if schedule_status == ScheduleResult.OUT_OF_SCHEDULE:
            return Response(
                {
                    "error": "Mesa de partes virtual solo recibe documentos dentro del horario de atención."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = ProcedureCreateSerializer(
            data=request.data,
            context={"request": request}
        )

        serializer.is_valid(raise_exception=True)

        procedures = serializer.save()
        procedure = procedures[0]

        first_flow = (
            ProcedureFlow.objects
            .filter(procedure=procedure)
            .order_by("sequence")
            .first()
        )

        # Enviar correo
        send_procedure_email(
            procedure=procedure,
            is_out_of_schedule=False
        )

        message = (
            "Su documento fue registrado correctamente. "
            "Se ha enviado un correo con su código de seguimiento: "
            f"{procedure.tracking_code}"
        )

        return Response(
            {
                "message": message,
                "status": first_flow.status,
                "code": procedure.tracking_code
            },
            status=status.HTTP_201_CREATED
        )

class ProcedureCodePreviewAPIView(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request):

        area_id = request.query_params.get("area")

        if not area_id:
            return Response(
                {"error": "area es requerido"},
                status=400
            )

        try:
            area = Area.objects.get(id=area_id)
        except Area.DoesNotExist:
            return Response(
                {"error": "Área no encontrada"},
                status=404
            )

        code = preview_procedure_code(area)

        serializer = ProcedureCodePreviewSerializer({
            "code": code
        })

        return Response(serializer.data)

class ProcedureCreateAPIView(APIView):

    def post(self, request):

        schedule_status = check_schedule(timezone.now())

        # ❌ Domingo o feriado
        if schedule_status == ScheduleResult.NO_LABORABLE:
            return Response(
                {
                    "error": "El registro de trámites no está disponible los domingos ni feriados."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        # ❌ Fuera de horario laboral
        if schedule_status == ScheduleResult.OUT_OF_SCHEDULE:
            return Response(
                {
                    "error": "El registro de trámites solo está disponible dentro del horario laboral."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = ProcedureCreateSerializer(
            data=request.data,
            context={"request": request}
        )

        serializer.is_valid(raise_exception=True)

        procedures = serializer.save()
        procedure = procedures[0]

        first_flow = (
            ProcedureFlow.objects
            .filter(procedure=procedure)
            .order_by("sequence")
            .first()
        )

        return Response(
            {
                "message": "Trámite registrado exitosamente.",
                "status": first_flow.status,
                "code": procedure.code
            },
            status=status.HTTP_201_CREATED
        )

class ProcedureUpdateAPIView(APIView):

    @transaction.atomic
    def put(self, request, pk):

        procedure = get_object_or_404(Procedure, pk=pk)

        serializer = ProcedureUpdateSerializer(
            procedure,
            data=request.data,
            context={
                "request": request,
                "procedure": procedure
            }
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        # 📎 Archivos nuevos
        for file in request.FILES.getlist("files"):
            ProcedureFile.objects.create(
                procedure=procedure,
                file=file,
                uploaded_by=request.user
            )

        # ❌ Archivos eliminados
        deleted_files = request.data.getlist("deleted_files")

        if deleted_files:
            ProcedureFile.objects.filter(
                id__in=deleted_files,
                procedure=procedure
            ).delete()

        return Response(
            {"message": "Trámite actualizado exitosamente"},
            status=status.HTTP_200_OK
        )

class ProcedureListAPIView(generics.ListAPIView):

    serializer_class = ProcedureListSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = ProcedureFilter

    def get_queryset(self):

        area = self.get_active_area()

        return (
            Procedure.objects.filter(from_area=area)
            .prefetch_related("files", "to_area")
            .order_by("-created_at")
        )

    def get_serializer_context(self):

        return {"request": self.request}
    
    def get_active_area(self):
        area_id = self.request.headers.get("X-Area-Id")
        print(area_id)
        if not area_id:
            return None
        return Area.objects.filter(id=area_id).first()

class ProcedureAnnulAPIView(APIView):

    def post(self, request, pk):

        procedure = get_object_or_404(Procedure, pk=pk)

        serializer = ProcedureAnnulSerializer(
            data=request.data,
            context={
                "request": request,
                "procedure": procedure
            }
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"message": "Trámite anulado correctamente"},
            status=status.HTTP_200_OK
        )

class UpdateProcedureCopiesAPIView(APIView):

    @transaction.atomic
    def put(self, request, pk):

        procedure = get_object_or_404(Procedure, pk=pk)

        serializer = ProcedureUpdateCopiesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_areas = serializer.validated_data["copy_areas"]

        # Borrar TODAS las copias anteriores
        ProcedureFlow.objects.filter(
            procedure=procedure,
            flow_type=ProcedureFlow.COPY
        ).delete()

        # 🔴 VALIDACIÓN DE HORARIO (NUEVO)
        schedule_status = check_schedule(timezone.now())    

        if schedule_status == ScheduleResult.NO_LABORABLE:

           return Response({"error": "Estimado usuario el registro de trámites no está disponible los domingos ni feriados."},status=status.HTTP_400_BAD_REQUEST)

        # DEFINIR ESTADO INICIAL DEL FLOW
        flow_status = ProcedureFlow.SENT
        registered_out_of_schedule_at = None

        if schedule_status == ScheduleResult.OUT_OF_SCHEDULE:

            flow_status = ProcedureFlow.PENDING_SCHEDULE
            registered_out_of_schedule_at = timezone.now()

        # ✅ Crear nuevas copias
        for area in new_areas:
            ProcedureFlow.objects.create(
                procedure=procedure,
                to_area=area,
                from_area=procedure.from_area,
                sent_by=request.user,
                flow_type=ProcedureFlow.COPY,
                status=flow_status,
                is_active=True,
                subject=procedure.subject,
                sequence = 1,
                registered_out_of_schedule_at=registered_out_of_schedule_at
            )

        # 🟢 MENSAJE GLOBAL (UNO SOLO)
        if flow_status == ProcedureFlow.PENDING_SCHEDULE:
            message = (
                "Las copias del trámite fueron registradas fuera del horario laboral "
                "y serán procesadas automáticamente el siguiente día hábil "
                "en el horario de atención."
            )
        else:
            message = "Las copias del trámite fueron registradas exitosamente."

        return Response(
            {
                "message": message,
                "status": flow_status
            },
            status=status.HTTP_200_OK
        )

class CopyDecisionAPIView(APIView):

    @transaction.atomic
    def put(self, request, pk):

        decision = request.data.get("decision")  # approved | rejected
        comment = request.data.get("comment", "")

        flow = get_object_or_404(
            ProcedureFlow,
            pk=pk,
            flow_type=ProcedureFlow.COPY   #  blindaje
        )

        procedure = flow.procedure

        if flow.status != ProcedureFlow.SENT:
            return Response(
                {"error": "La copia ya fue atendida"},
                status=400
            )

        if procedure.is_expired:

            return Response({"error": "Este trámite está bloqueado por fuera del plazo de finalización"}, status=400 )

        if decision == "approved":

            flow.status = ProcedureFlow.RECEIVED

        elif decision == "rejected":

            flow.status = ProcedureFlow.REJECTED

        else:
            
            return Response(
                {"error": "Decisión inválida"},
                status=400
            )

    
        flow.comment = comment
        flow.save(update_fields=["status", "comment"])

        return Response(
            {
                "success": True,
                "status": flow.status
            }
        )

# ----------- LIST MOVIMIENTOS
class ProcedureConsultAPIView(generics.ListAPIView):

    serializer_class = ProcedureListSerializer
    pagination_class = CustomPagination

    def get_queryset(self):

        qs = Procedure.objects.select_related(
            "from_area",
            "to_area",
            "agency",
            "created_by"
        ).order_by("-created_at")

        # filtros
        agency = self.request.query_params.get("agency")
        code = self.request.query_params.get("code")
        year = self.request.query_params.get("year")

        if agency:
            qs = qs.filter(agency_id=agency)

        if code:
            qs = qs.filter(
                Q(code__icontains=code) |
                Q(code_destino__icontains=code)
            )

        if year:
            qs = qs.filter(created_at__year=year)

        return qs

class VirtualFlowListAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination

    authentication_classes = []
    permission_classes = []

    def get_queryset(self):

        agency_id = self.request.query_params.get("agency")
        code = self.request.query_params.get("code")
        tracking_code = self.request.query_params.get("tracking_code")
        flow_type = self.request.query_params.get("type")  # TV | TE | TI

        # ❌ Debe venir al menos uno
        if not code and not tracking_code:
            return ProcedureFlow.objects.none()

        qs = (
            ProcedureFlow.objects
            .select_related(
                "procedure",
                "from_area",
                "to_area",
                "sent_by"
            )
            .filter(flow_type=ProcedureFlow.NORMAL)
            .exclude(status=ProcedureFlow.PENDING_SCHEDULE)
            .order_by("-sequence")
        )

        # Búsqueda interna
        if code:
            if not agency_id:
                return ProcedureFlow.objects.none()

            qs = qs.filter(
                Q(
                    procedure__code__icontains=code,
                    procedure__agency_id=agency_id
                )
                |
                Q(
                    procedure__code_destino__icontains=code,
                    procedure__to_area__agency_id=agency_id
                )
            )

        # Búsqueda pública (solo virtual)
        if tracking_code:
            qs = qs.filter(
                procedure__tracking_code=tracking_code,
                procedure__is_virtual=True
            )

        # 🔥 filtro dinámico por tipo
        if flow_type:
            qs = qs.filter(procedure__from_area__type=flow_type)

        return qs
    
# PENDIENTES
class PendingFlowListAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = PendingFlowFilter

    def get_queryset(self):
        area_id = self.request.headers.get("X-Area-Id")
        if not area_id:
            return ProcedureFlow.objects.none()

        return (
            ProcedureFlow.objects
            .filter(
                to_area_id=area_id,
                status=ProcedureFlow.SENT,
                is_active=True
            )
            .select_related("procedure", "from_area")
            .order_by("-created_at")
        )

# RECEPCIONADOS
class ReceptionFlowListAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = PendingFlowFilter

    def get_queryset(self):
        area_id = self.request.headers.get("X-Area-Id")

        if not area_id:
            return ProcedureFlow.objects.none()

        return (
            ProcedureFlow.objects
            .filter(
                to_area_id=area_id,
                flow_type=ProcedureFlow.NORMAL,
                status=ProcedureFlow.RECEIVED,
                is_active=True
            )
            .select_related("procedure", "from_area")
            .order_by("-created_at")
        )

# ENVIADOS
class SentFlowListAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = PendingFlowFilter

    def get_queryset(self):
        area_id = self.request.headers.get("X-Area-Id")

        if not area_id:
            return ProcedureFlow.objects.none()

        return (
            ProcedureFlow.objects
            .filter(
                from_area_id=area_id,
                # flow_type=ProcedureFlow.NORMAL,
                status=ProcedureFlow.SENT,
                is_to_observed=False,   #  clave
            )
            .select_related("procedure", "to_area")
            .order_by("-created_at")
        )

# COPIAS
class CopyInboxPendingAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination

    def get_queryset(self):

        area_id = self.request.headers.get("X-Area-Id")

        if not area_id:
            return ProcedureFlow.objects.none()

        return (
            ProcedureFlow.objects
            .filter(
                to_area_id=area_id,
                flow_type=ProcedureFlow.COPY,      # 👈 SOLO COPIAS
                status=ProcedureFlow.SENT
            )
            .select_related(
                "procedure",
                "from_area",
                "to_area"
            )
            .order_by("-created_at")
        )

class CopyInboxApprovedAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination

    def get_queryset(self):

        area_id = self.request.headers.get("X-Area-Id")

        if not area_id:
            return ProcedureFlow.objects.none()

        original_finalizado_subquery = ProcedureFlow.objects.filter(
            procedure_id=OuterRef("procedure_id"),
            flow_type=ProcedureFlow.NORMAL,
            status=ProcedureFlow.FINALIZED,
            is_active=True,
        )

        return (
            ProcedureFlow.objects
            .filter(
                to_area_id=area_id,
                flow_type=ProcedureFlow.COPY,
                status__in=[
                    ProcedureFlow.RECEIVED,
                    ProcedureFlow.FINALIZED,
                ],
                is_active=True
            )
            .annotate(
                original_finalizado=Exists(original_finalizado_subquery)
            )
            .select_related("procedure", "from_area", "to_area")
            .order_by("-created_at")
        )

# FINALIZADOS
class FinalizeFlowListAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = PendingFlowFilter

    def get_queryset(self):

        area_id = self.request.headers.get("X-Area-Id")

        if not area_id:
            return ProcedureFlow.objects.none()

        # Área actual
        try:
            area = Area.objects.select_related("agency").get(id=area_id)
        except Area.DoesNotExist:
            return ProcedureFlow.objects.none()

        qs = (
            ProcedureFlow.objects
            .filter(
                flow_type=ProcedureFlow.NORMAL,
                status=ProcedureFlow.FINALIZED,
                is_active=True
            )
        )

        # 🔵 Mesa de Partes ANDAHUAYLAS → ve TODOS
        if area.code == '001' or area.code == '002':
            return (
                qs
                .select_related("procedure", "from_area")
                .order_by("-created_at")
            )


        # 🟢 CASO 2: Otras agencias
        return (
            qs
            .filter(
                Q(to_area_id=area) |               # Finalizados en su área
                Q(procedure__from_area=area)        # O trámites de su agencia
            )
            .select_related("procedure", "from_area")
            .order_by("-created_at")
        )

# RECHAZADOS
class RejectInboxAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination

    def get_queryset(self):

        area_id = self.request.headers.get("X-Area-Id")

        if not area_id:
            return ProcedureFlow.objects.none()

        # Subquery: último SENT NORMAL antes del REJECTED
        last_sent_flow = ProcedureFlow.objects.filter(
            procedure=OuterRef("procedure"),
            flow_type=ProcedureFlow.NORMAL,
            status=ProcedureFlow.SENT,
            sequence__lt=OuterRef("sequence")
        ).order_by("-sequence")

        return (
            ProcedureFlow.objects
            .annotate(
                last_sender_area=Subquery(
                    last_sent_flow.values("from_area_id")[:1]
                )
            )
            .filter(
                flow_type=ProcedureFlow.NORMAL,
                status=ProcedureFlow.REJECTED,
                last_sender_area=area_id
            )
            .select_related("procedure", "from_area")
            .order_by("-created_at")
        )

# OBSERVADOS
class ObservedInboxAPIView(generics.ListAPIView):

    serializer_class = ProcedureFlowSerializer
    pagination_class = CustomPagination

    def get_queryset(self):

        area_id = self.request.headers.get("X-Area-Id")

        if not area_id:
            return ProcedureFlow.objects.none()

        # Subquery: último SENT NORMAL antes del REJECTED
        # last_sent_flow = ProcedureFlow.objects.filter(
        #     procedure=OuterRef("procedure"),
        #     flow_type=ProcedureFlow.NORMAL,
        #     status=ProcedureFlow.RECEIVED,
        #     sequence__lt=OuterRef("sequence")
        # ).order_by("-sequence")

        return (
            ProcedureFlow.objects
            # .annotate(
            #     last_sender_area=Subquery(
            #         last_sent_flow.values("from_area_id")[:1]
            #     )
            # )
            .filter(
                flow_type=ProcedureFlow.NORMAL,           
                status=ProcedureFlow.OBSERVED,
                from_area=area_id,
                is_active=True
            )
            .select_related("procedure", "from_area")
            .order_by("-created_at")
        )
    
# ----------- PROCEDURE MOVIMIENTOS

# RECEPCIONAR
class ReceiveProcedureFlowAPIView(APIView):

    def post(self, request, flow_id):

        try:
            flow = ProcedureFlow.objects.select_related(
                "procedure", "to_area"
            ).get(
                id=flow_id,
                is_active=True,
                flow_type=ProcedureFlow.NORMAL,                  
                status=ProcedureFlow.SENT
            )
        except ProcedureFlow.DoesNotExist:
            return Response(
                {"detail": "Pending flow not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = ReceiveFlowSerializer(
            data=request.data,
            context={
                "request": request,
                "flow": flow
            }
        )
        serializer.is_valid(raise_exception=True)
        new_flow = serializer.save()

        return Response(
            {
                "message": "Trámite recepcionado correctamente",
                "sequence": new_flow.sequence,
                "status": new_flow.status
            },
            status=status.HTTP_200_OK
        )

# RECHAZAR
class RejectProcedureFlowAPIView(APIView):

    def post(self, request, flow_id):

        try:
            flow = ProcedureFlow.objects.get(
                id=flow_id,
                flow_type=ProcedureFlow.NORMAL,   
                status=ProcedureFlow.SENT
            )
        except ProcedureFlow.DoesNotExist:
            return Response(
                {"detail": "Sent flow not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = RejectFlowSerializer(
            data=request.data,
            context={"request": request, "flow": flow}
        )
        serializer.is_valid(raise_exception=True)
        new_flow = serializer.save()


        if flow.procedure.is_virtual:

            send_procedure_rejected_email(
                procedure=flow.procedure,
                comment=new_flow.comment
            ) 

        return Response(
            {
                "message": f"Trámite {new_flow.status.lower()} correctamente",
            },
            status=status.HTTP_200_OK
        )

# DERIVAR
class DeriveProcedureFlowAPIView(APIView):
  
    def post(self, request, flow_id):

        try:
            flow = ProcedureFlow.objects.select_related(
                "procedure", "to_area"
            ).get(
                id=flow_id,
                flow_type=ProcedureFlow.NORMAL,   
                status=ProcedureFlow.RECEIVED
            )
        except ProcedureFlow.DoesNotExist:
            return Response(
                {"detail": "Received flow not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = DeriveFlowSerializer(
            data=request.data,
            context={"request": request, "flow": flow}
        )
        serializer.is_valid(raise_exception=True)
        created = serializer.save()

        return Response(
            {
                "message": "Trámite derivado correctamente",
                "created_flows": [
                    {
                        "id": f.id,
                        "sequence": f.sequence,
                        "flow_type": f.flow_type,
                        "to_area": f.to_area.name
                    } for f in created
                ]
            },
            status=status.HTTP_200_OK
        )

# OBSERVAR
class ObservedProcedureFlowAPIView(APIView):

    def post(self, request, flow_id):

        try:
            flow = ProcedureFlow.objects.get(
                id=flow_id,
                flow_type=ProcedureFlow.NORMAL,   
                status=ProcedureFlow.RECEIVED
            )
        except ProcedureFlow.DoesNotExist:
            return Response(
                {"detail": "Received flow not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = ObservedFlowSerializer(
            data=request.data,
            context={"request": request, "flow": flow}
        )
        serializer.is_valid(raise_exception=True)
        new_flow = serializer.save()

        return Response(
            {
                "message": f"Trámite {new_flow.status.lower()} correctamente",
            },
            status=status.HTTP_200_OK
        )

# FINALIZAR
class FinalizeProcedureFlowAPIView(APIView):

    def post(self, request, flow_id):

        try:

            flow = ProcedureFlow.objects.get(
                id=flow_id,
                flow_type=ProcedureFlow.NORMAL,   
                status=ProcedureFlow.RECEIVED
            )

        except ProcedureFlow.DoesNotExist:

            return Response(
                {"detail": "Received flow not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = FinalizeFlowSerializer(
            data=request.data,
            context={"request": request, "flow": flow}
        )
        serializer.is_valid(raise_exception=True)
        new_flow = serializer.save()

        return Response(
            {
                "message": "Trámite finalizado correctamente",
                "sequence": new_flow.sequence
            },
            status=status.HTTP_200_OK
        )

# REENVIAR    
class ResendObservedProcedureFlowAPIView(APIView):

    @transaction.atomic
    def post(self, request, flow_id):

        flow = get_object_or_404(
            ProcedureFlow,
            id=flow_id,
            flow_type=ProcedureFlow.NORMAL,
            status=ProcedureFlow.OBSERVED
        )

        serializer = ResendObservedFlowSerializer(
            data=request.data,
            context={"request": request, "flow": flow}
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        # NO guardar archivos aquí

        # 🗑️ Eliminación de archivos (esto sí puede quedar)
        deleted_files = request.data.getlist("deleted_files")

        if deleted_files:
            ProcedureFile.objects.filter(
                id__in=deleted_files,
                procedure=flow.procedure
            ).delete()

        return Response(
            {"message": "Trámite reenviado correctamente"},
            status=status.HTTP_200_OK
        )

# SUBSANAR
class SubsanarProcedureFlowAPIView(APIView):

    @transaction.atomic
    def post(self, request, flow_id):

        flow = get_object_or_404(
            ProcedureFlow,
            id=flow_id
        )

        serializer = SubsanarFlowSerializer(
            data=request.data,
            context={"request": request, "flow": flow}
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"message": "Trámite subsanado correctamente"},
            status=status.HTTP_200_OK
        )

# ------- PDF

# HISTORICO
class ProcedureHistoryPDFAPIView(APIView):

    def get(self, request, procedure_id):

        company = Company.objects.first()
        logo_path = request.build_absolute_uri("/media/logo.png")

        procedure = (
            Procedure.objects
            .select_related("created_by")
            .get(id=procedure_id)
        )

        flows = (
            ProcedureFlow.objects
            .filter(
                procedure=procedure,
                flow_type=ProcedureFlow.NORMAL, 
            )
            .select_related("from_area", "to_area")
            .order_by("sequence")
        )

        # 🔥 ENRIQUECER CADA FLOW CON SU STATUS GLOBAL
        flows_with_status = []
        for flow in flows:
            flows_with_status.append({
                "flow": flow,
                "status": get_flow_global_status_display(flow)
            })

        html_string = render_to_string(
            "reports/procedure_history.html",
            {
                "company": company,
                "procedure": procedure,
                "flows": flows_with_status,
                "company_logo": logo_path
            }
        )

        html = HTML(string=html_string, base_url=request.build_absolute_uri())
        pdf = html.write_pdf()

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="tramite_{procedure.code}.pdf"'
        )

        return response
#SIMPLICADO

class ProcedureHistorySimplicadoPDFAPIView(APIView):

    def get(self, request, procedure_id):

        company = Company.objects.first()
        logo_path = request.build_absolute_uri("/media/logo.png")

        procedure = (
            Procedure.objects
            .select_related("created_by")
            .get(id=procedure_id)
        )

        # 🟢 1. Primer flow (registro inicial)
        first_flow = (
            ProcedureFlow.objects
            .filter(
                procedure=procedure,
                flow_type=ProcedureFlow.NORMAL, 
            )
            .select_related("from_area", "to_area")
            .order_by("sequence")
            .first()
        )

        # 🟡 2. Último envío AUTORIZADO (Gerencia)
        authorized_flow = (
            ProcedureFlow.objects
            .filter(
                procedure=procedure,
                flow_type=ProcedureFlow.NORMAL, 
                status=ProcedureFlow.SENT,
                origin_options__contains=["AUTHORIZED"]
            )
            .select_related("from_area", "to_area")
            .order_by("-sequence")
            .first()
        )

         # 🔁 Fallback: último FINALIZED
        if not authorized_flow:
            authorized_flow = (
                ProcedureFlow.objects
                .filter(
                    procedure=procedure,
                    flow_type=ProcedureFlow.NORMAL, 
                    status=ProcedureFlow.FINALIZED
                )
                .select_related("from_area", "to_area")
                .order_by("-sequence")
                .first()
            )

        first_flow_status = get_flow_status_display(first_flow)
        authorized_flow_status = (
            get_flow_status_display(authorized_flow)
            if authorized_flow else None
        )

        html_string = render_to_string(
            "reports/procedure_history_simple.html",
            {
                "company": company,
                "procedure": procedure,
                "first_flow": first_flow,
                "authorized_flow": authorized_flow,
                "company_logo": logo_path,
                "first_flow_status": first_flow_status,
                "authorized_flow_status": authorized_flow_status,
            }
        )

        html = HTML(
            string=html_string,
            base_url=request.build_absolute_uri()
        )

        pdf = html.write_pdf()

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="tramite_{procedure.code}_resumen.pdf"'
        )

        return response

# TICKET
class TicketProcedureAPIView(APIView):

    def get(self, request, procedure_id):

        company = Company.objects.first()
        logo_path = request.build_absolute_uri(f"/media/logo.png")

        procedure = (
            Procedure.objects
            .select_related("created_by")
            .get(id=procedure_id)
        )

        # EJEMPLO
        codigo = "mFn067"

        seguimiento_url = f"https://tu-dominio.pe/seguimiento/{codigo}"

        qr_base64 = generar_qr_base64(seguimiento_url)

        html_string = render_to_string(
            "ticket/ticket.html",
            {
              "procedure": procedure,
              "company" : company,
              "codigo": codigo,
              "qr_base64": qr_base64,
              "company_logo": logo_path
            }
        )

        html = HTML(string=html_string, base_url=request.build_absolute_uri())

        pdf = html.write_pdf()

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="ticket_{codigo}.pdf"'
        return response

# DASHBOARD

class FlowDashboardAPIView(APIView):

    STATUS_TITLES = {
        "pending": "Pendientes",
        "received": "Recepcionados",
        "sent": "Enviados",
        "observed": "Observados",
        "rejected": "Rechazados",
        "finalized": "Finalizados",
    }

    def get(self, request):

        area_id = request.headers.get("X-Area-Id")

        if not area_id:
            return Response(
                {"detail": "X-Area-Id header required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        area = Area.objects.select_related("agency").get(id=area_id)
    
        # 🔹 SUBQUERY: último envío previo
        last_sent_flow = ProcedureFlow.objects.filter(
            procedure=OuterRef("procedure"),
            flow_type=ProcedureFlow.NORMAL,
            status=ProcedureFlow.SENT,
            sequence__lt=OuterRef("sequence")
        ).order_by("-sequence")

        # 🔹 BASE QUERYSETS

        pending_base = ProcedureFlow.objects.filter(
        
            status=ProcedureFlow.SENT,
            to_area_id=area_id,
            is_active=True
        )

        received_base = ProcedureFlow.objects.filter(
            flow_type=ProcedureFlow.NORMAL,
            status=ProcedureFlow.RECEIVED,
            to_area_id=area_id,
            is_active=True
        )

        sent_base = ProcedureFlow.objects.filter(
          
            status=ProcedureFlow.SENT,
            from_area_id=area_id,
            is_to_observed=False
        )

        finalized_base = ProcedureFlow.objects.filter(
            flow_type=ProcedureFlow.NORMAL,
            status=ProcedureFlow.FINALIZED,
            is_active=True
        )
    
        if area.code not in ['001', '002']:
         
            finalized_base = finalized_base.filter(Q(to_area_id=area) | Q(procedure__from_area=area)  )

        observed_base = (
            ProcedureFlow.objects
            .filter(
                flow_type=ProcedureFlow.NORMAL,
                status=ProcedureFlow.OBSERVED,
                from_area=area_id,
                is_active=True
            )
        )

        rejected_base = (
            ProcedureFlow.objects
            .annotate(
                last_sender_area=Subquery(
                    last_sent_flow.values("from_area_id")[:1]
                )
            )
            .filter(
                flow_type=ProcedureFlow.NORMAL,
                status=ProcedureFlow.REJECTED,
                last_sender_area=area_id,
                is_active=True
            )
        )

        # 🔹 Helper: separar por tipo TE / TI
        def split_by_type(qs):
            return {
                "TE": qs.filter(procedure__from_area__type__in=["TE", "TV"]).count(),
                "TI": qs.filter(procedure__from_area__type="TI").count(),
            }

        # 🔹 Construcción final del dashboard
        data = []

        sources = {
            "pending": pending_base,
            "received": received_base,
            "sent": sent_base,
            "finalized": finalized_base,
            "observed": observed_base,
            "rejected": rejected_base,
        }

        for key, qs in sources.items():
            counts = split_by_type(qs)

            data.append({
                "key": key,
                "title": self.STATUS_TITLES[key],
                "TE": counts["TE"],
                "TI": counts["TI"],
                "total": counts["TE"] + counts["TI"],
            })

        return Response(data, status=status.HTTP_200_OK)

# SISTEMA DE BACKUPS

class GlobalBackupView(APIView):

    def get(self, request):

        backups = GlobalBackup.objects.all().order_by("-created_at")
        serializer = GlobalBackupSerializer(backups, many=True)
        return Response(serializer.data)

    def post(self, request):

        user = request.user.username if request.user.is_authenticated else "manual"
        call_command("backup_global", user=user)
        return Response(
            {"message": "Backup global iniciado"},
            status=status.HTTP_201_CREATED
        )
       
class GlobalBackupDownloadView(APIView):

    def get(self, request, backup_id):

        try:
            backup = GlobalBackup.objects.get(id=backup_id)

        except GlobalBackup.DoesNotExist:

            raise Http404("Backup no encontrado")

        if not os.path.exists(backup.file_path):

            raise Http404("Archivo no existe en el servidor")

        response = FileResponse(
            open(backup.file_path, "rb"),
            as_attachment=True,
            filename=backup.file_name
        )

        return response

class BackupCreateView(APIView):

    def post(self, request):

        # carpeta origen
        source = os.path.join(settings.MEDIA_ROOT, "procedures")

        # nombre archivo
        filename = f"procedures_manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"

        # carpeta destino
        backup_dir = os.path.join(settings.BACKUP_ROOT, "manual")

        # crear carpeta si no existe
        os.makedirs(backup_dir, exist_ok=True)

        destination = os.path.join(backup_dir, filename)

        # ejecutar backup
        subprocess.run([
            "tar",
            "-czf",
            destination,
            source
        ], check=True)

        size = os.path.getsize(destination)

        backup = SystemBackup.objects.create(
            file_path=destination,
            size=size,
            backup_type="manual"
        )

        return Response({
            "message": "Backup creado correctamente",
            "backup_id": backup.id
        })
    
class BackupListView(generics.ListAPIView):

    queryset = SystemBackup.objects.all()
    serializer_class = SystemBackupSerializer

class BackupDownloadView(APIView):

    def get(self, request, pk):

        try:
            backup = SystemBackup.objects.get(pk=pk)
        except SystemBackup.DoesNotExist:
            raise Http404

        if not os.path.exists(backup.file_path):
            raise Http404("Archivo no existe")

        return FileResponse(
            open(backup.file_path, "rb"),
            as_attachment=True,
            filename=backup.filename
        )
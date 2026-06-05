from rest_framework import routers
from django.urls import path
from .views import CompanyViewSet, GlobalBackupView, GlobalBackupDownloadView, CheckScheduleAPIView, ProcedureConsultAPIView, SubsanarProcedureFlowAPIView, ProcedureCodePreviewAPIView, ProcedureHistorySimplicadoPDFAPIView, HolidayViewSet, WorkScheduleViewSet, ProcedureListVirtualesAPIView, VirtualFlowListAPIView, ProcedureVirtualCreateAPIView, UpdateProcedureCopiesAPIView, CopyInboxPendingAPIView, CopyInboxApprovedAPIView, TicketProcedureAPIView, ProcedureAnnulAPIView, ProcedureUpdateAPIView, SentFlowListAPIView, FlowDashboardAPIView, ProcedureHistoryPDFAPIView, ResendObservedProcedureFlowAPIView, RejectInboxAPIView, ObservedInboxAPIView, ObservedProcedureFlowAPIView, FinalizeFlowListAPIView, RejectProcedureFlowAPIView, PendingFlowListAPIView, FinalizeProcedureFlowAPIView, DeriveProcedureFlowAPIView, ReceptionFlowListAPIView, ReceiveProcedureFlowAPIView, ProcedureListAPIView, MyAreasView, AreaViewSet, DocumentViewSet, AgencyViewSet, ProcedureCreateAPIView, DepartmentListAPIView, ProvinceListAPIView, DistrictListAPIView, CopyDecisionAPIView, BackupListView, BackupCreateView, BackupDownloadView

router = routers.DefaultRouter()

router.register("company", CompanyViewSet)
router.register("areas", AreaViewSet)
router.register("documents", DocumentViewSet)
router.register('agencies', AgencyViewSet)
router.register("holiday", HolidayViewSet)
router.register('work', WorkScheduleViewSet)

urlpatterns = [

    path("global-backup/", GlobalBackupView.as_view()),
    path("global-backup/<int:backup_id>/download/", GlobalBackupDownloadView.as_view()),
    path("check-schedule/", CheckScheduleAPIView.as_view()),

    path("departments/", DepartmentListAPIView.as_view()),
    path("provinces/", ProvinceListAPIView.as_view()),
    path("districts/", DistrictListAPIView.as_view()),

    path('areas/user/', MyAreasView.as_view()),
    path('flows/', VirtualFlowListAPIView.as_view()),
    path('list-virtual-procedure/', ProcedureListVirtualesAPIView.as_view()),

    path('list-tramite/', ProcedureListAPIView.as_view()),
    path('create-tramite/', ProcedureCreateAPIView.as_view()),
    path('virtual-procedure/', ProcedureVirtualCreateAPIView.as_view()),
    path('update-procedure/<int:pk>/', ProcedureUpdateAPIView.as_view()),
    path('annulled-procedure/<int:pk>/', ProcedureAnnulAPIView.as_view()),
    path("history-procedure/<int:procedure_id>/pdf/", ProcedureHistoryPDFAPIView.as_view()),
    path("history-procedure-simplificado/<int:procedure_id>/pdf/", ProcedureHistorySimplicadoPDFAPIView.as_view()),
    path("ticket-procedure/<int:procedure_id>/pdf/", TicketProcedureAPIView.as_view()),

    path("copies-procedure/<int:pk>/", UpdateProcedureCopiesAPIView.as_view()),

    path("copies-pending/", CopyInboxPendingAPIView.as_view()),
    path("copies-approved/", CopyInboxApprovedAPIView.as_view()),
    
    path("copies-decision/<int:pk>/", CopyDecisionAPIView.as_view()),

    # CONSULTAS

    path('consult-procedure/', ProcedureConsultAPIView.as_view()),

    # MOVIMIENTOS  

    path("pending/", PendingFlowListAPIView.as_view()),
    path("reception/", ReceptionFlowListAPIView.as_view()),
    path("finalize/", FinalizeFlowListAPIView.as_view()),
    path("reject/", RejectInboxAPIView.as_view()),
    path("observed/", ObservedInboxAPIView.as_view()),
    path("sent/", SentFlowListAPIView.as_view()),

    path("flows/<int:flow_id>/receive/", ReceiveProcedureFlowAPIView.as_view()), 
    path("flows/<int:flow_id>/derive/", DeriveProcedureFlowAPIView.as_view()),
    path("flows/<int:flow_id>/finalize/", FinalizeProcedureFlowAPIView.as_view()),
    path("flows/<int:flow_id>/reject/", RejectProcedureFlowAPIView.as_view()),
    path("flows/<int:flow_id>/observed/", ObservedProcedureFlowAPIView.as_view()),
    path("flows/<int:flow_id>/resend-observed/", ResendObservedProcedureFlowAPIView.as_view()),
    path("flows/<int:flow_id>/subsanar/", SubsanarProcedureFlowAPIView.as_view()),

    path("dashboard/flows/",  FlowDashboardAPIView.as_view()),
    path("procedures/preview-code/", ProcedureCodePreviewAPIView.as_view(),name="procedure-preview-code"),

    path("backups/", BackupListView.as_view()),
    path("backups/create/", BackupCreateView.as_view()),
    path("backups/<int:pk>/download/", BackupDownloadView.as_view()),

] + router.urls
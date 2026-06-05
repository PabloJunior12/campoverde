from django.contrib.auth import authenticate
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.viewsets import ModelViewSet
from rest_framework.pagination import PageNumberPagination
from .serializers import UserSerializer, ModuleSerializer, UserPermissionSerializer, UserToggleSerializer, ChangePasswordSerializer
from .models import User, Module, UserPermission
from apps.tramite.models import UserArea
from .services import get_allowed_modules
import requests

class CustomPagination(PageNumberPagination):

    page_size = 5  # Número de registros por página
    page_size_query_param = 'page_size'  # Permite cambiar el tamaño desde la URL
    max_page_size = 100  # Tamaño máximo permitido

class LoginView(APIView):

    permission_classes = [AllowAny]

    def post(self, request):

        agency_id = request.data.get('agency')
        username = request.data.get('username')
        password = request.data.get('password')
        # tenant_name = request.data.get('tenant')

        if not username or not password:
            return Response({"error": "Se requieren usuario y contraseña."}, status=400)

        user = authenticate(request, username=username, password=password)

        if user is None:
            return Response({"error": "Credenciales inválidas."}, status=401)

        if not user.is_active:
            return Response({"error": "Cuenta desactivada."}, status=403)

        # ✅ VALIDAR AGENCIA
        if user.agency_id != int(agency_id):
            return Response(
                {"error": "La agencia seleccionada no pertenece a este usuario."},
                status=403
            )

        # 🔒 VALIDACIÓN DE TENANT
        # tenant_name = (tenant_name or "").lower().strip()

        # Caso 1: entorno público
        # if tenant_name == "public":
        #     if user.tenant:
        #         return Response(
        #             {"error": "Este usuario pertenece a un tenant y no puede acceder al entorno público."},
        #             status=403
        #         )

        # Caso 2: entorno de tenant
        # else:
        #     if not user.tenant:
        #         return Response(
        #             {"error": "Este usuario es global y no pertenece a ningún tenant."},
        #             status=403
        #         )

        #     if user.tenant.schema_name != tenant_name:
        #         return Response(
        #             {"error": f"El usuario no pertenece al tenant '{tenant_name}'."},
        #             status=403
        #         )

        # ✅ Si pasa todas las validaciones, emitir token
        token, _ = Token.objects.get_or_create(user=user)
        permissions = UserPermission.objects.filter(user=user).select_related('module')
  
        permissions_data = [
            {"module_id": perm.module.id, "module": perm.module.code, "name": perm.module.name}
            for perm in permissions
        ]

   
        user_data = {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "is_admin": user.is_admin,
            "is_staff": user.is_staff,
            "is_active": user.is_active,
            "can_void_procedure": user.can_void_procedure,
            "can_view_options": user.can_view_options,
            "can_finalize_procedure": user.can_finalize_procedure,
            "token": token.key,
            "permissions": permissions_data,
        
        }

        return Response(user_data, status=200)
      
class LogoutView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        try:
             
            request.user.auth_token.delete()
            return Response({"message": "Logout exitoso."}, status=200)
        
        except:

             return Response({"error": "Error al realizar el logout."}, status=400)

class ProtectedView(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request):

        return Response({"message": "Accediste a una ruta protegida"}, status=200)
    
class RucApiView(APIView):

    authentication_classes = [] 
    permission_classes = []    

    def get(self, request, number):

        # Construcción del endpoint y encabezados
        url = f"https://apifoxperu.net/api/ruc/{number}"
        token = "JDuaRQyRDjiD6a6NpMXdRHoKiOfsUxksnbFRNNK0"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            # Solicitud al servicio externo
            response = requests.get(url, headers=headers, timeout=10)

            # Validar respuesta
            if response.status_code == 200:
                return Response(response.json())
            else:
                return Response(
                    {"error": f"Error al consultar el servicio externo. details {response.json()}"}, status=response.status_code,
                )
        except requests.RequestException as e:
            # Manejo de excepciones en caso de error de conexión o tiempo de espera
            return Response(
                {"error": f"Error al conectar con el servicio externo. details {str(e)}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        
class DniApiView(APIView):

    authentication_classes = [] 
    permission_classes = []    
    
    def get(self, request, number):

        # Construcción del endpoint y encabezados
        url = f"https://apifoxperu.net/api/dni/{number}"
        token = "JDuaRQyRDjiD6a6NpMXdRHoKiOfsUxksnbFRNNK0"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            # Solicitud al servicio externo
            response = requests.get(url, headers=headers, timeout=10)

            # Validar respuesta
            if response.status_code == 200:
                return Response(response.json())
            else:
                return Response(
                    response.json(), status=response.status_code,
                )
        except requests.RequestException as e:
            # Manejo de excepciones en caso de error de conexión o tiempo de espera
            return Response(
                {"error": f"Error al conectar con el servicio externo. details {str(e)}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        
class UserViewSet(ModelViewSet):

    queryset = User.objects.all().order_by('id')
    serializer_class = UserSerializer
    pagination_class = CustomPagination

    def get_queryset(self):

        user = self.request.user

        # 🧩 Superusuario global (staff=True, tenant=None)
        if user.is_staff:
            return User.objects.all().order_by('id')

        # 🧩 Administrador de tenant (is_admin=True, tenant=X)
        elif user.is_admin:
            return User.objects.exclude(is_staff=True).order_by('id')

        # 🧩 Usuario normal (solo si quieres permitirle verse a sí mismo)
        # elif not user.is_admin and user.tenant is not None:
        #     return User.objects.filter(id=user.id)

        # 🧩 Cualquier otro caso
        return User.objects.none()
    
    @action(
        detail=True,
        methods=['post'],
        permission_classes=[IsAuthenticated]
    )
    def change_password(self, request, pk=None):

        user = self.get_object()
        serializer = ChangePasswordSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 🔐 set_password SIEMPRE
        user.set_password(serializer.validated_data['password'])
        user.save()

        return Response(
            {"message": "Contraseña actualizada correctamente"},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=["patch"])
    def toggles(self, request, pk=None):

        user = self.get_object()

        if "is_active" in request.data:

            if not request.user.is_admin:
               
               return Response({"error": "No autorizado"}, status=401)

            if request.user.id == user.id:
                 
               return Response({"error": "No puede desactivarse a sí mismo"}, status=401)

        serializer = UserToggleSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(serializer.data)

class ChangeMyPasswordView(APIView):
    
    permission_classes = [IsAuthenticated]

    def post(self, request):

        user = request.user  # 👈 viene del token
        serializer = ChangePasswordSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(serializer.validated_data['password'])
        user.save()

        return Response(
            {"message": "Contraseña actualizada correctamente"},
            status=status.HTTP_200_OK
        )

class ModuleViewSet(ModelViewSet):

    queryset = Module.objects.all().order_by('id')
    serializer_class = ModuleSerializer

class UserPermissionViewSet(ModelViewSet):

    queryset = UserPermission.objects.all()
    serializer_class = UserPermissionSerializer

    def get_queryset(self):

        user_id = self.request.query_params.get('user')
        if user_id:
            return UserPermission.objects.filter(user_id=user_id)
        return super().get_queryset()

class MeView(APIView):
    
    permission_classes = [IsAuthenticated]

    def get(self, request):

        user = request.user

        # módulos permitidos (incluye padres)
        root_modules = get_allowed_modules(user)
        module_tree = ModuleSerializer(root_modules, many=True).data

        user_data = {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "is_admin": user.is_admin,
            "is_staff": user.is_staff,
            "is_active": user.is_active,
            "can_void_procedure": user.can_void_procedure,
            "can_view_options": user.can_view_options,
            "can_finalize_procedure": user.can_finalize_procedure,
            "modules": module_tree,
           
        }

        return Response(user_data, status=200)


# serializers.py
from rest_framework import serializers
from apps.tramite.serializers import UserAreaSerializer
from apps.tramite.models import UserArea
from .models import User, Module, UserPermission, GlobalPermission

class ChangePasswordSerializer(serializers.Serializer):

    password = serializers.CharField(
        write_only=True,
        min_length=6
    )
    confirm = serializers.CharField(write_only=True)

    def validate(self, data):
        if data['password'] != data['confirm']:
            raise serializers.ValidationError({
                "confirm": "Las contraseñas no coinciden"
            })
        return data

class ModuleSerializer(serializers.ModelSerializer):

    children = serializers.SerializerMethodField()

    class Meta:
        model = Module
        fields = ["id", "name", "code", "icon", "children", "path"]

    def get_children(self, obj):
        children = obj.children.all().order_by("order")
        return ModuleSerializer(children, many=True).data
    
class UserPermissionSerializer(serializers.ModelSerializer):

    module = serializers.PrimaryKeyRelatedField(queryset=Module.objects.all())

    class Meta:
        model = UserPermission
        fields = ['module']

class GlobalPermissionSerializer(serializers.ModelSerializer):

    class Meta:
        model = GlobalPermission
        fields = ['allowed_actions']

class UserSerializer(serializers.ModelSerializer):

    agency_name = serializers.CharField(source='agency.name', read_only=True)
    global_permissions = GlobalPermissionSerializer(required=False)
    permissions = UserPermissionSerializer(many=True, required=False)
    areas = UserAreaSerializer(source='user_areas', many=True, required=False)
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'name', 'username', 'surname', 'phone', 'can_void_procedure', 'can_view_options', 'can_finalize_procedure',
            'is_active', 'is_staff', 'is_admin', 'password', 'permissions','global_permissions','areas','agency','agency_name'
        ]

    def create(self, validated_data):

        global_data = validated_data.pop('global_permissions', None)
        permissions_data = validated_data.pop('permissions', [])
        areas_data = validated_data.pop('user_areas', [])
        password = validated_data.pop('password', None)

        user = User(**validated_data)
 
        if password:

            user.set_password(password)

        user.save()

        # Crear permisos asociados
        for perm in permissions_data:
            UserPermission.objects.create(user=user, module=perm['module'])

        # 🌍 Crear permisos globales
        GlobalPermission.objects.create(
            user=user,
            **(global_data or {'allowed_actions': []})
        )

        # 🏢 Áreas
        for area in areas_data:

            UserArea.objects.create(user=user, area=area['area'])

            return user

    def update(self, instance, validated_data):

        global_data = validated_data.pop('global_permissions', None)
        permissions_data = validated_data.pop('permissions', None)
        areas_data = validated_data.pop('user_areas', None)
        password = validated_data.pop('password', None)

        # Actualizar campos básicos
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()

        if global_data is not None:
            gp, _ = GlobalPermission.objects.get_or_create(user=instance)
            gp.allowed_actions = global_data.get('allowed_actions', [])
            gp.save()

        # Actualizar permisos si vienen en el request
        if permissions_data is not None:
            instance.permissions.all().delete()  # limpiar permisos actuales
            for perm in permissions_data:
                UserPermission.objects.create(user=instance, module=perm['module'])

        # 🏢 Actualizar áreas
        if areas_data is not None:
           instance.user_areas.all().delete()
           for area in areas_data:
               UserArea.objects.create(user=instance, area=area['area'])

        return instance

class UserToggleSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = (
            "is_active",    
            "can_void_procedure",
            "can_view_options",
            "can_finalize_procedure",
        )

import json
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()


class Command(BaseCommand):
    help = "Migrar usuarios desde JSON antiguo (sin áreas)"

    def handle(self, *args, **options):

        with open("media/user.json", encoding="utf-8") as f: 
            data = json.load(f)

        created = 0
        skipped = 0

        for item in data:
            username = item["username"]

            if User.objects.filter(username=username).exists():
                self.stdout.write(self.style.WARNING(
                    f"⏭ Usuario ya existe: {username}"
                ))
                skipped += 1
                continue

            email = item["email"] or f"{username}@migracion.local"

            with transaction.atomic():
                user = User.objects.create(
                    username=username,
                    email=email,
                    name=item["name"],
                    surname=item.get("surname"),
                    is_active=item.get("is_active", True),

                    # roles
                    is_admin=False,
                    is_staff=False,

                    # permisos funcionales
                    can_view_options=item.get("view_checkboxes", False),
                    can_finalize_procedure=item.get("is_finalizar_tramite", False),
                    can_void_procedure=item.get("is_annul_historico", False),

                    # agencia
                    agency_id=item.get("agency_id", 1),
                )

                user.set_password("123456")
                user.save()

                created += 1

                self.stdout.write(self.style.SUCCESS(
                    f"✅ Usuario creado: {username}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"\n🎉 Migración finalizada → {created} creados | {skipped} omitidos"
        ))

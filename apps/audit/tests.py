"""
apps/audit/tests.py

Tests for AuditLog + log_event — fully implemented foundation pieces,
so these are real behavioral tests (not just model-shape tests like the
other apps' Prompt-1 test files, since log_event has actual logic).
"""

from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import ProviderProfile, User
from apps.common.enums import RoleChoices
from apps.scholarships.models import Scholarship

from .models import AuditLog
from .services import log_event


class LogEventTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            username="admin1", password="a-strong-pass-123", role=RoleChoices.ADMIN
        )

    def test_log_event_without_related_object(self):
        entry = log_event(
            actor=self.admin_user,
            event_type=AuditLog.EventType.LOGIN,
            description="Admin logged in.",
        )
        self.assertEqual(entry.actor, self.admin_user)
        self.assertEqual(entry.event_type, AuditLog.EventType.LOGIN)
        self.assertIsNone(entry.related_object)

    def test_log_event_with_related_object_sets_generic_fk(self):
        provider_user = User.objects.create_user(
            username="provider_audit", password="a-strong-pass-123", role=RoleChoices.PROVIDER
        )
        provider = ProviderProfile.objects.create(
            user=provider_user, organization_name="Audit Test Org", contact_email="x@example.com",
        )
        scholarship = Scholarship.objects.create(
            provider=provider, title="Audit Test Scholarship", description="desc",
            amount=Decimal("10000.00"), deadline=timezone.now() + timezone.timedelta(days=5),
        )
        entry = log_event(
            actor=self.admin_user,
            event_type=AuditLog.EventType.SCHOLARSHIP_CREATED,
            related_object=scholarship,
            description="Created scholarship.",
        )
        self.assertEqual(entry.related_object, scholarship)
        self.assertEqual(entry.related_object_id, scholarship.pk)

    def test_log_event_extracts_ip_from_request(self):
        request = RequestFactory().get("/")
        request.META["REMOTE_ADDR"] = "203.0.113.5"
        entry = log_event(
            actor=self.admin_user,
            event_type=AuditLog.EventType.LOGIN,
            request=request,
        )
        self.assertEqual(entry.ip_address, "203.0.113.5")

    def test_log_event_prefers_x_forwarded_for(self):
        request = RequestFactory().get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.7, 10.0.0.1"
        entry = log_event(
            actor=self.admin_user,
            event_type=AuditLog.EventType.LOGIN,
            request=request,
        )
        self.assertEqual(entry.ip_address, "198.51.100.7")

    def test_log_event_without_request_has_no_ip(self):
        entry = log_event(actor=self.admin_user, event_type=AuditLog.EventType.LOGOUT)
        self.assertIsNone(entry.ip_address)

    def test_system_initiated_event_allows_null_actor(self):
        entry = log_event(actor=None, event_type=AuditLog.EventType.OTHER, description="System event.")
        self.assertIsNone(entry.actor)

    def test_ordering_is_most_recent_first(self):
        first = log_event(actor=self.admin_user, event_type=AuditLog.EventType.LOGIN)
        second = log_event(actor=self.admin_user, event_type=AuditLog.EventType.LOGOUT)
        entries = list(AuditLog.objects.all())
        self.assertEqual(entries[0], second)
        self.assertEqual(entries[1], first)

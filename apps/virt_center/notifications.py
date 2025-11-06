#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
虚拟化中心通知消息
"""

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from apps.notifications.backends import BACKEND
from apps.notifications.models import SystemMsgSubscription
from apps.notifications.notifications import SystemMessage
from apps.system.models import UserInfo


class PlatformSyncSuccessMessage(SystemMessage):
    """平台同步成功消息"""

    category = "VirtCenter"
    category_label = _("Virtualization Center")
    message_type_label = _("Platform sync success")

    def __init__(self, platform_name, sync_result):
        self.platform_name = platform_name
        self.sync_result = sync_result

    def get_html_msg(self) -> dict:
        subject = _("Platform {} synchronization completed").format(self.platform_name)

        # 统计同步结果
        results = self.sync_result.get("results", [])
        total_synced = 0
        total_failed = 0

        for r in results:
            if isinstance(r, dict):
                # 结果格式：{"task": "sync_hosts", "result": {"synced_count": 10, ...}}
                result_data = r.get("result", {})
                if isinstance(result_data, dict):
                    total_synced += result_data.get("synced_count", 0)
                    total_failed += result_data.get("failed_count", 0)

        context = {
            "platform_name": self.platform_name,
            "sync_time": self.sync_result.get("sync_time", ""),
            "total_synced": total_synced,
            "total_failed": total_failed,
            "results": results,
        }

        message = render_to_string("virt_center/msg_sync_success.html", context)
        return {
            "subject": subject,
            "message": message,
        }

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "success"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        """设置默认订阅用户为管理员"""
        admins = UserInfo.objects.filter(is_superuser=True, is_active=True)
        subscription.users.add(*admins)
        subscription.receive_backends = [BACKEND.SITE_MSG]
        subscription.save()

    @classmethod
    def gen_test_msg(cls):
        return cls(
            platform_name="测试平台",
            sync_result={
                "sync_time": "2024-01-01 10:00:00",
                "results": [
                    {"synced_count": 10, "failed_count": 0},
                    {"synced_count": 50, "failed_count": 0},
                ],
            },
        )


class PlatformSyncFailedMessage(SystemMessage):
    """平台同步失败消息"""

    category = "VirtCenter"
    category_label = _("Virtualization Center")
    message_type_label = _("Platform sync failed")

    def __init__(self, platform_name, error):
        self.platform_name = platform_name
        self.error = error

    def get_html_msg(self) -> dict:
        subject = _("Platform {} synchronization failed").format(self.platform_name)

        context = {
            "platform_name": self.platform_name,
            "error": self.error,
        }

        message = render_to_string("virt_center/msg_sync_failed.html", context)
        return {
            "subject": subject,
            "message": message,
        }

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "danger"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        """设置默认订阅用户为管理员"""
        admins = UserInfo.objects.filter(is_superuser=True, is_active=True)
        subscription.users.add(*admins)
        subscription.receive_backends = [BACKEND.SITE_MSG, BACKEND.EMAIL]
        subscription.save()

    @classmethod
    def gen_test_msg(cls):
        return cls(
            platform_name="测试平台",
            error="连接超时：无法连接到 vCenter 服务器",
        )


class PlatformStatusChangedMessage(SystemMessage):
    """平台状态变化消息"""

    category = "VirtCenter"
    category_label = _("Virtualization Center")
    message_type_label = _("Platform status changed")

    def __init__(self, platform_name, old_status, new_status):
        self.platform_name = platform_name
        self.old_status = old_status
        self.new_status = new_status

    def get_html_msg(self) -> dict:
        subject = _("Platform {} status changed").format(self.platform_name)

        # 状态映射
        status_map = {
            0: _("Disconnected"),
            1: _("Connected"),
            2: _("Error"),
            3: _("Maintenance"),
        }

        context = {
            "platform_name": self.platform_name,
            "old_status": status_map.get(self.old_status, str(self.old_status)),
            "new_status": status_map.get(self.new_status, str(self.new_status)),
        }

        message = render_to_string("virt_center/msg_status_changed.html", context)
        return {
            "subject": subject,
            "message": message,
        }

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        # 根据新状态设置消息级别
        if self.new_status == 2:  # ERROR
            info["level"] = "danger"
        elif self.new_status == 1:  # CONNECTED
            info["level"] = "success"
        else:
            info["level"] = "warning"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        """设置默认订阅用户为管理员"""
        admins = UserInfo.objects.filter(is_superuser=True, is_active=True)
        subscription.users.add(*admins)
        subscription.receive_backends = [BACKEND.SITE_MSG]
        subscription.save()

    @classmethod
    def gen_test_msg(cls):
        return cls(
            platform_name="测试平台",
            old_status=1,
            new_status=2,
        )


class HostOperationSuccessMessage(SystemMessage):
    """主机操作成功消息"""

    category = "VirtCenter"
    category_label = _("Virtualization Center")
    message_type_label = _("Host operation success")

    def __init__(self, host_name, operation, operator_name=None):
        self.host_name = host_name
        self.operation = operation
        self.operator_name = operator_name

    def get_html_msg(self) -> dict:
        # 操作映射
        operation_map = {
            "enter_maintenance": _("Enter maintenance mode"),
            "exit_maintenance": _("Exit maintenance mode"),
            "reboot": _("Reboot"),
            "shutdown": _("Shutdown"),
        }

        operation_display = operation_map.get(self.operation, self.operation)
        subject = _("Host {} operation completed: {}").format(self.host_name, operation_display)

        context = {
            "host_name": self.host_name,
            "operation": operation_display,
            "operator_name": self.operator_name,
        }

        message = render_to_string("virt_center/msg_host_operation_success.html", context)
        return {
            "subject": subject,
            "message": message,
        }

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "success"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        """设置默认订阅用户为管理员"""
        admins = UserInfo.objects.filter(is_superuser=True, is_active=True)
        subscription.users.add(*admins)
        subscription.receive_backends = [BACKEND.SITE_MSG]
        subscription.save()

    @classmethod
    def gen_test_msg(cls):
        return cls(
            host_name="esxi-01.example.com",
            operation="enter_maintenance",
            operator_name="admin",
        )


class HostOperationFailedMessage(SystemMessage):
    """主机操作失败消息"""

    category = "VirtCenter"
    category_label = _("Virtualization Center")
    message_type_label = _("Host operation failed")

    def __init__(self, host_name, operation, error, operator_name=None):
        self.host_name = host_name
        self.operation = operation
        self.error = error
        self.operator_name = operator_name

    def get_html_msg(self) -> dict:
        # 操作映射
        operation_map = {
            "enter_maintenance": _("Enter maintenance mode"),
            "exit_maintenance": _("Exit maintenance mode"),
            "reboot": _("Reboot"),
            "shutdown": _("Shutdown"),
        }

        operation_display = operation_map.get(self.operation, self.operation)
        subject = _("Host {} operation failed: {}").format(self.host_name, operation_display)

        context = {
            "host_name": self.host_name,
            "operation": operation_display,
            "error": self.error,
            "operator_name": self.operator_name,
        }

        message = render_to_string("virt_center/msg_host_operation_failed.html", context)
        return {
            "subject": subject,
            "message": message,
        }

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "danger"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        """设置默认订阅用户为管理员"""
        admins = UserInfo.objects.filter(is_superuser=True, is_active=True)
        subscription.users.add(*admins)
        subscription.receive_backends = [BACKEND.SITE_MSG, BACKEND.EMAIL]
        subscription.save()

    @classmethod
    def gen_test_msg(cls):
        return cls(
            host_name="esxi-01.example.com",
            operation="enter_maintenance",
            error="无法进入维护模式：主机上仍有运行中的虚拟机",
            operator_name="admin",
        )

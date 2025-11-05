#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
宿主机管理视图
"""

from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from apps.common.core.modelset import BaseModelSet
from apps.common.core.response import ApiResponse
from apps.common.swagger.utils import get_default_response_schema
from apps.virt_center.decorators import record_operation
from apps.virt_center.filters import HostFilter
from apps.virt_center.models import Host
from apps.virt_center.serializers import (
    HostDetailSerializer,
    HostListSerializer,
    HostOperationSerializer,
    HostSerializer,
)


class HostViewSet(BaseModelSet):
    """宿主机管理视图集"""

    queryset = Host.objects.select_related("platform").all()
    serializer_class = HostListSerializer
    list_serializer_class = HostListSerializer
    ordering_fields = ["created_time", "updated_time", "name", "cpu_usage", "memory_usage"]
    filterset_class = HostFilter

    def get_serializer_class(self):
        """根据操作返回不同的序列化器"""
        if self.action == "list":
            return HostListSerializer
        elif self.action == "retrieve":
            return HostDetailSerializer
        elif self.action in ["create", "update", "partial_update"]:
            return HostSerializer
        return HostListSerializer

    @extend_schema(responses=get_default_response_schema())
    def retrieve(self, request, *args, **kwargs):
        """获取主机详情（包括资源、网络、虚拟机）"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return ApiResponse(data=serializer.data)

    @extend_schema(
        request=HostOperationSerializer,
        responses=get_default_response_schema(),
    )
    @action(methods=["POST"], detail=True, url_path="operate")
    @record_operation("operate", "host")
    def operate_host(self, request, *args, **kwargs):
        """执行主机操作（维护模式、重启等）"""
        host = self.get_object()
        serializer = HostOperationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        operation = serializer.validated_data["operation"]
        force = serializer.validated_data.get("force", False)

        try:
            from apps.virt_center.services.vsphere_client import get_vsphere_client

            client = get_vsphere_client(host.platform)

            with client:
                # 根据操作类型执行相应操作
                if operation == "enter_maintenance":
                    result = client.enter_maintenance_mode(host.mo_ref)
                    message = "主机已进入维护模式"
                    # 更新本地状态
                    host.in_maintenance = True
                    host.save(update_fields=["in_maintenance", "updated_time"])
                elif operation == "exit_maintenance":
                    result = client.exit_maintenance_mode(host.mo_ref)
                    message = "主机已退出维护模式"
                    # 更新本地状态
                    host.in_maintenance = False
                    host.save(update_fields=["in_maintenance", "updated_time"])
                elif operation == "reboot":
                    result = client.reboot_host(host.mo_ref, force=force)
                    message = "主机重启命令已发送"
                elif operation == "shutdown":
                    result = client.shutdown_host(host.mo_ref, force=force)
                    message = "主机关闭命令已发送"
                else:
                    return ApiResponse(code=1001, msg=_("Invalid operation type"))

                return ApiResponse(
                    data={
                        "operation": operation,
                        "host_name": host.name,
                        "result": result,
                        "message": message,
                    },
                    msg=_("Operation successful"),
                )

        except Exception as e:
            return ApiResponse(
                code=1001,
                msg=_("Operation failed"),
                data={"error": str(e), "operation": operation},
            )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["POST"], detail=True, url_path="sync")
    @record_operation("sync_data", "host")
    def sync_host(self, request, *args, **kwargs):
        """同步单个主机的数据"""
        host = self.get_object()

        try:
            from apps.virt_center.tasks import sync_host_detail

            # 异步执行同步任务
            result = sync_host_detail.delay(str(host.id))

            return ApiResponse(
                data={
                    "task_id": result.id,
                    "host_id": str(host.id),
                    "host_name": host.name,
                    "message": "同步任务已启动",
                },
                msg=_("Sync task started"),
            )

        except Exception as e:
            return ApiResponse(
                code=1001,
                msg=_("Failed to start sync task"),
                data={"error": str(e)},
            )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["GET"], detail=False, url_path="statistics")
    def statistics(self, request):
        """获取主机统计数据"""
        queryset = self.filter_queryset(self.get_queryset())

        total_count = queryset.count()
        online_count = queryset.filter(status=Host.Status.ONLINE).count()
        offline_count = queryset.filter(status=Host.Status.OFFLINE).count()
        maintenance_count = queryset.filter(in_maintenance=True).count()

        # 资源统计
        from django.db.models import Avg, Sum

        resource_stats = queryset.aggregate(
            total_cpu_cores=Sum("cpu_cores"),
            total_cpu_threads=Sum("cpu_threads"),
            total_memory_mb=Sum("memory_total"),
            total_vms=Sum("vm_count"),
            avg_cpu_usage=Avg("cpu_usage"),
            avg_memory_usage=Avg("memory_usage"),
        )

        # 厂商统计
        vendor_stats = {}
        for host in queryset.values("vendor").distinct():
            vendor = host["vendor"] or "Unknown"
            count = queryset.filter(vendor=host["vendor"]).count()
            vendor_stats[vendor] = count

        return ApiResponse(
            data={
                "total_count": total_count,
                "online_count": online_count,
                "offline_count": offline_count,
                "maintenance_count": maintenance_count,
                "vendor_stats": vendor_stats,
                "resource_stats": {
                    "total_cpu_cores": resource_stats["total_cpu_cores"] or 0,
                    "total_cpu_threads": resource_stats["total_cpu_threads"] or 0,
                    "total_memory_gb": round((resource_stats["total_memory_mb"] or 0) / 1024, 2),
                    "total_vms": resource_stats["total_vms"] or 0,
                    "avg_cpu_usage": round(resource_stats["avg_cpu_usage"] or 0, 2),
                    "avg_memory_usage": round(resource_stats["avg_memory_usage"] or 0, 2),
                },
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["GET"], detail=True, url_path="vms")
    def list_vms(self, request, *args, **kwargs):
        """获取主机上的所有虚拟机"""
        host = self.get_object()
        from apps.virt_center.models import VirtualMachine
        from apps.virt_center.serializers import VirtualMachineListSerializer

        vms = VirtualMachine.objects.filter(host=host, is_active=True)
        serializer = VirtualMachineListSerializer(vms, many=True)
        return ApiResponse(data=serializer.data, total=vms.count())

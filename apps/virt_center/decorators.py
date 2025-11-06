#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
虚拟化中心装饰器
"""

from functools import wraps

from django.utils import timezone

from apps.common.utils import get_logger
from apps.virt_center.models import Host, OperationLog, Platform, VirtualMachine

logger = get_logger(__name__)


def record_operation(operation_type, target_type="platform"):
    """
    记录操作日志装饰器

    Args:
        operation_type: 操作类型
        target_type: 目标类型

    Usage:
        @record_operation("sync_data", "platform")
        def sync_platform(self, request, *args, **kwargs):
            platform = self.get_object()
            ...
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, request, *args, **kwargs):
            # 获取目标对象
            target_obj = self.get_object() if hasattr(self, "get_object") else None

            # 根据目标对象类型获取 platform
            platform = None
            target_id = ""
            target_name = ""

            if target_obj:
                if isinstance(target_obj, Platform):
                    platform = target_obj
                    target_id = str(target_obj.id)
                    target_name = target_obj.name
                elif isinstance(target_obj, Host):
                    platform = target_obj.platform
                    target_id = str(target_obj.id)
                    target_name = target_obj.name
                elif isinstance(target_obj, VirtualMachine):
                    platform = target_obj.platform
                    target_id = str(target_obj.id)
                    target_name = target_obj.name
                else:
                    # 如果对象有 platform 属性，尝试获取
                    if hasattr(target_obj, "platform"):
                        platform = target_obj.platform
                    target_id = str(target_obj.id) if hasattr(target_obj, "id") else ""
                    target_name = getattr(target_obj, "name", "")

            # 创建操作日志
            log = OperationLog.objects.create(
                platform=platform,
                operator=request.user if request.user.is_authenticated else None,
                operation_type=operation_type,
                target_type=target_type,
                target_id=target_id,
                target_name=target_name,
                status=OperationLog.Status.RUNNING,
                parameters={"action": func.__name__, **request.data} if hasattr(request, "data") else {},
            )

            try:
                # 执行原函数
                response = func(self, request, *args, **kwargs)

                # 更新操作日志为成功
                log.status = OperationLog.Status.SUCCESS
                log.result = getattr(response, "data", {}).get("message", "操作成功")
                log.end_time = timezone.now()

                # 计算执行时长
                if log.start_time:
                    duration = log.end_time - log.start_time
                    log.duration_seconds = int(duration.total_seconds())

                log.save()

                return response

            except Exception as e:
                # 更新操作日志为失败
                log.status = OperationLog.Status.FAILED
                log.error_message = str(e)
                log.end_time = timezone.now()

                # 计算执行时长
                if log.start_time:
                    duration = log.end_time - log.start_time
                    log.duration_seconds = int(duration.total_seconds())

                log.save()
                raise

        return wrapper

    return decorator

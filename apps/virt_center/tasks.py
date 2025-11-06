#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
虚拟化中心 Celery 任务
支持手动调度和定时调度
"""

from contextlib import contextmanager
from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from celery import shared_task
from celery.exceptions import Retry
from celery.utils.log import get_task_logger

from apps.common.celery.decorator import after_app_ready_start, register_as_period_task

from .models import DataStore, Host, HostMetrics, OperationTask, Platform, VirtualMachine, VMMetrics, VMTemplate
from .services.data_parser import (
    parse_datastore_info,
    parse_host_info,
    parse_host_networks,
    parse_host_resource,
    parse_platform_info,
    parse_template_info,
    parse_vm_disks,
    parse_vm_info,
    parse_vm_networks,
    parse_vm_snapshots,
)
from .services.operation import create_operation_task, update_operation_task_status
from .services.sync_data import (
    sync_datastore_to_db,
    sync_host_networks_to_db,
    sync_host_resource_to_db,
    sync_host_to_db,
    sync_platform_to_db,
    sync_template_to_db,
    sync_vm_disks_to_db,
    sync_vm_networks_to_db,
    sync_vm_snapshots_to_db,
    sync_vm_to_db,
)
from .services.vsphere_client import get_vsphere_client
from .signal import platform_status_changed, platform_sync_completed, platform_sync_failed

logger = get_task_logger(__name__)


# ==================== 公共辅助函数 ====================


def get_active_platform(platform_id: str) -> Optional[Platform]:
    """
    获取激活的平台对象

    Args:
        platform_id: 平台ID

    Returns:
        Platform对象或None

    Raises:
        Platform.DoesNotExist: 平台不存在或未启用
    """
    try:
        return Platform.objects.get(id=platform_id, is_active=True)
    except Platform.DoesNotExist:
        logger.error(f"平台不存在或未启用: {platform_id}")
        raise


@contextmanager
def handle_platform_connection(platform: Platform):
    """
    处理平台连接的上下文管理器，自动处理连接错误和状态更新

    Args:
        platform: 平台对象

    Yields:
        vSphere客户端对象
    """
    client = get_vsphere_client(platform)
    old_status = platform.status

    try:
        with client:
            yield client
    except Exception as e:
        logger.error(f"连接平台失败 {platform.name}: {str(e)}")
        # 更新平台状态为异常
        if platform.status != Platform.Status.ERROR:
            platform.status = Platform.Status.ERROR
            platform.save(update_fields=["status", "updated_time"])

            # 发送状态变化信号
            platform_status_changed.send(
                sender=Platform,
                platform_id=str(platform.id),
                old_status=old_status,
                new_status=platform.status,
            )
        raise


def bulk_mark_inactive(queryset, exclude_field: str, exclude_values: list):
    """
    批量标记不存在的对象为不活跃

    Args:
        queryset: Django查询集
        exclude_field: 排除字段名
        exclude_values: 排除的值列表
    """
    if not exclude_values:
        return

    updated = (
        queryset.filter(is_active=True)
        .exclude(**{f"{exclude_field}__in": exclude_values})
        .update(is_active=False, updated_time=timezone.now())
    )

    if updated > 0:
        logger.info(f"标记了 {updated} 个对象为不活跃")


def create_task_result(success: bool, **kwargs) -> Dict[str, Any]:
    """
    创建标准的任务结果字典

    Args:
        success: 是否成功
        **kwargs: 其他结果字段

    Returns:
        结果字典
    """
    result = {
        "success": success,
        "sync_time": timezone.now().isoformat(),
    }
    result.update(kwargs)
    return result


# ==================== 核心同步任务 ====================


@shared_task(
    bind=True,
    name="virt_center.sync_platform_info",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
)
def sync_platform_info(self, platform_id: str) -> Dict[str, Any]:
    """
    同步平台信息

    Args:
        platform_id: 平台ID

    Returns:
        包含同步结果的字典
    """
    logger.info(f"开始同步平台信息: platform_id={platform_id}")

    # 获取平台对象
    platform = get_active_platform(platform_id)

    # 使用上下文管理器处理连接
    with handle_platform_connection(platform) as client:
        # 获取平台信息
        about_info = client.get_about_info()
        logger.info(f"获取到平台信息: {about_info.get('full_name')}")

        # 获取数据中心
        datacenters = client.get_datacenters()
        datacenter_names = [dc.name for dc in datacenters]

        # 获取集群、主机、虚拟机统计
        clusters = client.get_clusters()
        hosts = client.get_hosts()
        vms = client.get_vms()

        # 解析平台信息
        platform_data = parse_platform_info(
            about_info=about_info,
            datacenters=datacenter_names,
            cluster_count=len(clusters),
            host_count=len(hosts),
            vm_count=len(vms),
        )

        # 同步到数据库
        sync_platform_to_db(platform, platform_data)

        logger.info(f"平台信息同步完成: {platform.name}")

        return create_task_result(
            success=True,
            platform_id=platform_id,
            platform_name=platform.name,
            version=platform_data.get("version"),
            total_hosts=len(hosts),
            total_vms=len(vms),
        )


@shared_task(
    bind=True,
    name="virt_center.sync_hosts",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
)
def sync_hosts(self, platform_id: str) -> Dict[str, Any]:
    """
    同步主机信息

    Args:
        platform_id: 平台ID

    Returns:
        包含同步结果的字典
    """
    logger.info(f"开始同步主机信息: platform_id={platform_id}")

    # 获取平台对象
    platform = get_active_platform(platform_id)

    # 创建任务记录
    create_operation_task(
        platform=platform,
        task_type="sync_hosts",
        task_name=f"同步平台 {platform.name} 主机信息",
        celery_task_id=self.request.id,
        parameters={"platform_id": platform_id},
    )
    update_operation_task_status(
        celery_task_id=self.request.id,
        status=OperationTask.Status.RUNNING,
    )

    synced_count = 0
    failed_count = 0
    host_uuids = []

    try:
        with handle_platform_connection(platform) as client:
            # 获取所有主机
            hosts = client.get_hosts()
            total_hosts = len(hosts)
            logger.info(f"从 vSphere 获取到 {total_hosts} 个主机")

            for index, host in enumerate(hosts, 1):
                try:
                    # 解析主机信息
                    host_data = parse_host_info(host)
                    host_uuids.append(host_data["uuid"])

                    # 使用事务同步主机相关数据
                    with transaction.atomic():
                        # 同步主机基本信息到数据库
                        host_obj = sync_host_to_db(platform, host_data)

                        # 同步主机资源详情
                        resource_data = parse_host_resource(host)
                        sync_host_resource_to_db(host_obj, resource_data)

                        # 同步主机网络配置
                        networks_data = parse_host_networks(host)
                        sync_host_networks_to_db(host_obj, networks_data)

                    synced_count += 1
                    logger.debug(f"同步主机成功 [{index}/{total_hosts}]: {host_data['name']}")

                    # 更新进度
                    if index % 5 == 0 or index == total_hosts:  # 每5个或最后一个更新一次进度
                        progress = int((index / total_hosts) * 100)
                        update_operation_task_status(
                            celery_task_id=self.request.id,
                            status=OperationTask.Status.RUNNING,
                            progress=progress,
                            current_step=f"同步主机 {index}/{total_hosts}",
                        )

                except Exception as e:
                    logger.error(f"同步主机失败 {host.name}: {str(e)}", exc_info=True)
                    failed_count += 1
                    continue

            # 批量标记不再存在的主机
            bulk_mark_inactive(Host.objects.filter(platform=platform), "uuid", host_uuids)

            logger.info(f"主机同步完成: 成功={synced_count}, 失败={failed_count}")

            result_data = create_task_result(
                success=True,
                platform_id=platform_id,
                platform_name=platform.name,
                synced_count=synced_count,
                failed_count=failed_count,
                total_count=total_hosts,
            )

            # 更新任务状态为成功
            update_operation_task_status(
                celery_task_id=self.request.id,
                status=OperationTask.Status.SUCCESS,
                result=result_data,
                progress=100,
            )

            return result_data

    except Exception as e:
        logger.error(f"同步主机信息失败: {str(e)}")
        update_operation_task_status(
            celery_task_id=self.request.id,
            status=OperationTask.Status.FAILED,
            error_message=str(e),
        )
        raise


@shared_task(
    bind=True,
    name="virt_center.sync_host_detail",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
)
def sync_host_detail(self, host_id: str) -> Dict[str, Any]:
    """
    同步单个主机的详细信息（包括资源、网络等）

    Args:
        host_id: 主机ID

    Returns:
        包含同步结果的字典
    """
    logger.info(f"开始同步主机详细信息: host_id={host_id}")

    # 获取主机对象（使用 select_related 优化查询）
    try:
        host = Host.objects.select_related("platform").get(id=host_id, is_active=True)
    except Host.DoesNotExist:
        error_msg = f"主机不存在或未启用: {host_id}"
        logger.error(error_msg)
        return create_task_result(success=False, error=error_msg)

    platform = host.platform
    if not platform.is_active:
        error_msg = f"主机所属平台未启用: {platform.name}"
        logger.error(error_msg)
        return create_task_result(success=False, error=error_msg)

    with handle_platform_connection(platform) as client:
        # 获取主机对象（通过名称查找）
        hosts = client.get_hosts()
        host_vim = next((h for h in hosts if h.name == host.name), None)

        if not host_vim:
            error_msg = f"在 vSphere 中未找到主机: {host.name}"
            logger.error(error_msg)
            return create_task_result(success=False, error=error_msg)

        # 使用事务同步主机相关数据
        with transaction.atomic():
            # 解析并同步主机基本信息
            host_data = parse_host_info(host_vim)
            host_obj = sync_host_to_db(platform, host_data)

            # 同步主机资源详情
            resource_data = parse_host_resource(host_vim)
            sync_host_resource_to_db(host_obj, resource_data)

            # 同步主机网络配置
            networks_data = parse_host_networks(host_vim)
            sync_host_networks_to_db(host_obj, networks_data)

        logger.info(f"主机详细信息同步完成: {host_obj.name}")

        return create_task_result(
            success=True,
            host_id=host_id,
            host_name=host_obj.name,
        )


@shared_task(
    bind=True,
    name="virt_center.operate_host",
    max_retries=2,
    default_retry_delay=30,
)
def operate_host_task(self, host_id: str, operation: str, force: bool = False, operator_id: str = None):
    """
    异步执行主机操作（维护模式、重启、关闭等）

    Args:
        host_id: 主机ID
        operation: 操作类型（enter_maintenance, exit_maintenance, reboot, shutdown）
        force: 是否强制执行
        operator_id: 操作人员ID
    """
    try:
        logger.info(f"开始执行主机操作: host_id={host_id}, operation={operation}")

        # 获取主机对象
        try:
            host = Host.objects.select_related("platform").get(id=host_id, is_active=True)
        except Host.DoesNotExist:
            logger.error(f"主机不存在或未启用: {host_id}")
            return {"success": False, "error": "主机不存在或未启用"}

        platform = host.platform
        if not platform.is_active:
            logger.error(f"主机所属平台未启用: {platform.name}")
            return {"success": False, "error": "主机所属平台未启用"}

        # 获取操作人员
        operator = None
        if operator_id:
            try:
                from apps.system.models import UserInfo

                operator = UserInfo.objects.get(id=operator_id)
            except UserInfo.DoesNotExist:
                logger.warning(f"操作人员不存在: {operator_id}")

        # 创建操作任务记录
        operation_task = create_operation_task(
            platform=platform,
            task_type=f"host_{operation}",
            task_name=f"{operation} - {host.name}",
            celery_task_id=self.request.id,
            parameters={"host_id": host_id, "operation": operation, "force": force},
            creator=operator,
        )
        update_operation_task_status(
            celery_task_id=self.request.id,
            status=OperationTask.Status.RUNNING,
        )

        # 连接 vSphere
        client = get_vsphere_client(platform)

        try:
            with client:
                result = None
                message = ""

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
                    error_msg = f"无效的操作类型: {operation}"
                    logger.error(error_msg)
                    update_operation_task_status(
                        celery_task_id=self.request.id,
                        status=OperationTask.Status.FAILED,
                        error_message=error_msg,
                    )
                    return {"success": False, "error": error_msg}

                logger.info(f"主机操作完成: {host.name} - {operation}")

                result_data = {
                    "success": True,
                    "host_id": host_id,
                    "host_name": host.name,
                    "operation": operation,
                    "message": message,
                    "result": result,
                }

                # 更新任务状态为成功
                update_operation_task_status(
                    celery_task_id=self.request.id,
                    status=OperationTask.Status.SUCCESS,
                    result=result_data,
                    progress=100,
                )

                # 发送成功通知
                try:
                    from apps.virt_center.notifications import HostOperationSuccessMessage

                    # 获取操作人员
                    operator_name = None
                    if operator:
                        operator_name = operator.username

                    HostOperationSuccessMessage(
                        host_name=host.name, operation=operation, operator_name=operator_name
                    ).publish()
                    logger.debug(f"已发送主机 {host.name} 操作成功通知")
                except Exception as e:
                    logger.error(f"发送主机操作成功通知失败: {str(e)}")

                return result_data

        except Exception as e:
            logger.error(f"执行主机操作失败: {str(e)}")
            update_operation_task_status(
                celery_task_id=self.request.id,
                status=OperationTask.Status.FAILED,
                error_message=str(e),
            )

            # 发送失败通知
            try:
                from apps.virt_center.notifications import HostOperationFailedMessage

                # 获取操作人员
                operator_name = None
                if operator:
                    operator_name = operator.username

                HostOperationFailedMessage(
                    host_name=host.name, operation=operation, error=str(e), operator_name=operator_name
                ).publish()
                logger.debug(f"已发送主机 {host.name} 操作失败通知")
            except Exception as notify_error:
                logger.error(f"发送主机操作失败通知失败: {str(notify_error)}")

            raise

    except Exception as exc:
        logger.error(f"主机操作任务失败: {str(exc)}")
        update_operation_task_status(
            celery_task_id=self.request.id,
            status=OperationTask.Status.FAILED,
            error_message=str(exc),
        )
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name="virt_center.sync_vms",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
)
def sync_vms(self, platform_id: str) -> Dict[str, Any]:
    """
    同步虚拟机信息

    Args:
        platform_id: 平台ID

    Returns:
        包含同步结果的字典
    """
    logger.info(f"开始同步虚拟机信息: platform_id={platform_id}")

    # 获取平台对象
    platform = get_active_platform(platform_id)

    synced_count = 0
    failed_count = 0
    vm_uuids = []

    with handle_platform_connection(platform) as client:
        # 获取所有虚拟机
        vms = client.get_vms()
        logger.info(f"从 vSphere 获取到 {len(vms)} 个虚拟机")

        for vm in vms:
            try:
                # 解析虚拟机信息
                vm_data = parse_vm_info(vm)

                # 跳过模板（模板在单独的任务中同步）
                if vm_data.get("is_template"):
                    continue

                vm_uuids.append(vm_data["uuid"])

                # 使用事务同步虚拟机相关数据
                with transaction.atomic():
                    # 同步虚拟机基本信息到数据库
                    vm_obj = sync_vm_to_db(platform, vm_data)

                    # 同步虚拟机磁盘信息
                    disks_data = parse_vm_disks(vm)
                    sync_vm_disks_to_db(vm_obj, disks_data)

                    # 同步虚拟机网络信息
                    networks_data = parse_vm_networks(vm)
                    sync_vm_networks_to_db(vm_obj, networks_data)

                    # 同步虚拟机快照信息
                    snapshots_data = parse_vm_snapshots(vm)
                    sync_vm_snapshots_to_db(vm_obj, snapshots_data)

                synced_count += 1
                logger.debug(f"同步虚拟机成功: {vm_data['name']}")

            except Exception as e:
                logger.error(f"同步虚拟机失败 {vm.name}: {str(e)}", exc_info=True)
                failed_count += 1
                continue

        # 批量标记不再存在的虚拟机
        bulk_mark_inactive(VirtualMachine.objects.filter(platform=platform, is_template=False), "uuid", vm_uuids)

        logger.info(f"虚拟机同步完成: 成功={synced_count}, 失败={failed_count}")

        return create_task_result(
            success=True,
            platform_id=platform_id,
            platform_name=platform.name,
            synced_count=synced_count,
            failed_count=failed_count,
            total_count=len(vms),
        )


@shared_task(
    bind=True,
    name="virt_center.sync_datastores",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
)
def sync_datastores(self, platform_id: str):
    """
    同步数据存储信息

    Args:
        platform_id: 平台ID
    """
    try:
        logger.info(f"开始同步数据存储信息: platform_id={platform_id}")

        # 获取平台对象
        try:
            platform = Platform.objects.get(id=platform_id, is_active=True)
        except Platform.DoesNotExist:
            logger.error(f"平台不存在或未启用: {platform_id}")
            return {"success": False, "error": "平台不存在或未启用"}

        # 连接 vSphere
        client = get_vsphere_client(platform)

        synced_count = 0
        failed_count = 0
        datastore_names = []

        try:
            with client:
                # 获取所有数据存储
                datastores = client.get_datastores()
                logger.info(f"从 vSphere 获取到 {len(datastores)} 个数据存储")

                for datastore in datastores:
                    try:
                        # 解析数据存储信息
                        ds_data = parse_datastore_info(datastore)
                        datastore_names.append(ds_data["name"])

                        # 同步到数据库
                        sync_datastore_to_db(platform, ds_data)
                        synced_count += 1

                        logger.debug(f"同步数据存储成功: {ds_data['name']}")

                    except Exception as e:
                        logger.error(f"同步数据存储失败 {datastore.name}: {str(e)}")
                        failed_count += 1
                        continue

                # 标记不再存在的数据存储
                DataStore.objects.filter(platform=platform, is_active=True).exclude(name__in=datastore_names).update(
                    is_active=False, updated_time=timezone.now()
                )

                logger.info(f"数据存储同步完成: 成功={synced_count}, 失败={failed_count}")

                return {
                    "success": True,
                    "platform_id": platform_id,
                    "synced_count": synced_count,
                    "failed_count": failed_count,
                    "sync_time": timezone.now().isoformat(),
                }

        except Exception as e:
            logger.error(f"连接或获取数据存储信息失败: {str(e)}")
            raise

    except Exception as exc:
        logger.error(f"同步数据存储信息失败: {str(exc)}")
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name="virt_center.sync_templates",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
)
def sync_templates(self, platform_id: str):
    """
    同步虚拟机模板信息

    Args:
        platform_id: 平台ID
    """
    try:
        logger.info(f"开始同步模板信息: platform_id={platform_id}")

        # 获取平台对象
        try:
            platform = Platform.objects.get(id=platform_id, is_active=True)
        except Platform.DoesNotExist:
            logger.error(f"平台不存在或未启用: {platform_id}")
            return {"success": False, "error": "平台不存在或未启用"}

        # 连接 vSphere
        client = get_vsphere_client(platform)

        synced_count = 0
        failed_count = 0
        template_uuids = []

        try:
            with client:
                # 获取所有虚拟机（包括模板）
                vms = client.get_vms()
                logger.info(f"从 vSphere 获取虚拟机列表，查找模板...")

                for vm in vms:
                    try:
                        # 检查是否为模板
                        if not vm.config or not vm.config.template:
                            continue

                        # 解析模板信息
                        template_data = parse_template_info(vm)
                        template_uuids.append(template_data["uuid"])

                        # 同步到数据库
                        sync_template_to_db(platform, template_data)
                        synced_count += 1

                        logger.debug(f"同步模板成功: {template_data['name']}")

                    except Exception as e:
                        logger.error(f"同步模板失败 {vm.name}: {str(e)}")
                        failed_count += 1
                        continue

                # 标记不再存在的模板
                VMTemplate.objects.filter(platform=platform, is_active=True).exclude(uuid__in=template_uuids).update(
                    is_active=False, updated_time=timezone.now()
                )

                logger.info(f"模板同步完成: 成功={synced_count}, 失败={failed_count}")

                return {
                    "success": True,
                    "platform_id": platform_id,
                    "synced_count": synced_count,
                    "failed_count": failed_count,
                    "sync_time": timezone.now().isoformat(),
                }

        except Exception as e:
            logger.error(f"连接或获取模板信息失败: {str(e)}")
            raise

    except Exception as exc:
        logger.error(f"同步模板信息失败: {str(exc)}")
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    name="virt_center.sync_all_platform_data",
)
def sync_all_platform_data(self, platform_id: str):
    """
    同步平台所有数据（平台信息、主机、虚拟机、存储、模板）

    Args:
        platform_id: 平台ID

    Returns:
        dict: 同步结果
    """
    logger.info(f"开始同步平台所有数据: platform_id={platform_id}")

    # 创建任务记录
    operation_task = None

    try:
        # 检查平台是否存在
        try:
            platform = Platform.objects.get(id=platform_id, is_active=True)
        except Platform.DoesNotExist:
            logger.error(f"平台不存在或未启用: {platform_id}")
            return {"success": False, "error": "平台不存在或未启用"}

        # 创建操作任务记录
        operation_task = create_operation_task(
            platform=platform,
            task_type="sync_platform",
            task_name=f"同步平台 {platform.name} 所有数据",
            celery_task_id=self.request.id,
            parameters={"platform_id": platform_id},
        )

        # 更新任务状态为运行中
        update_operation_task_status(
            celery_task_id=self.request.id,
            status=OperationTask.Status.RUNNING,
            current_step="开始同步",
        )

        # 顺序执行所有同步任务（避免在任务内调用 .get()）
        results = []

        # 同步平台信息
        try:
            result = sync_platform_info(platform_id)
            results.append({"task": "sync_platform_info", "result": result})
        except Exception as e:
            logger.error(f"同步平台信息失败: {str(e)}")
            results.append({"task": "sync_platform_info", "error": str(e)})

        # 同步主机
        try:
            result = sync_hosts(platform_id)
            results.append({"task": "sync_hosts", "result": result})
        except Exception as e:
            logger.error(f"同步主机失败: {str(e)}")
            results.append({"task": "sync_hosts", "error": str(e)})

        # 同步虚拟机
        try:
            result = sync_vms(platform_id)
            results.append({"task": "sync_vms", "result": result})
        except Exception as e:
            logger.error(f"同步虚拟机失败: {str(e)}")
            results.append({"task": "sync_vms", "error": str(e)})

        # 同步数据存储
        try:
            result = sync_datastores(platform_id)
            results.append({"task": "sync_datastores", "result": result})
        except Exception as e:
            logger.error(f"同步数据存储失败: {str(e)}")
            results.append({"task": "sync_datastores", "error": str(e)})

        # 同步模板
        try:
            result = sync_templates(platform_id)
            results.append({"task": "sync_templates", "result": result})
        except Exception as e:
            logger.error(f"同步模板失败: {str(e)}")
            results.append({"task": "sync_templates", "error": str(e)})

        logger.info(f"平台所有数据同步完成: {platform.name}")

        sync_result = {
            "success": True,
            "platform_id": platform_id,
            "platform_name": platform.name,
            "results": results,
            "sync_time": timezone.now().isoformat(),
        }

        # 更新任务状态为成功
        update_operation_task_status(
            celery_task_id=self.request.id,
            status=OperationTask.Status.SUCCESS,
            result=sync_result,
            progress=100,
            current_step="同步完成",
        )

        # 发送同步完成信号
        platform_sync_completed.send(
            sender=Platform,
            platform_id=platform_id,
            platform_name=platform.name,
            sync_result=sync_result,
        )

        return sync_result

    except Exception as exc:
        logger.error(f"同步平台所有数据失败: {str(exc)}")

        error_result = {
            "success": False,
            "platform_id": platform_id,
            "error": str(exc),
        }

        # 更新任务状态为失败
        update_operation_task_status(
            celery_task_id=self.request.id,
            status=OperationTask.Status.FAILED,
            error_message=str(exc),
        )

        # 发送同步失败信号
        try:
            platform = Platform.objects.get(id=platform_id)
            platform_sync_failed.send(
                sender=Platform,
                platform_id=platform_id,
                platform_name=platform.name,
                error=str(exc),
            )
        except Exception:
            pass

        return error_result


@shared_task
@register_as_period_task(interval=3600)
@after_app_ready_start
def sync_all_platforms():
    """定期同步所有启用的平台数据"""
    logger.info("开始定期同步所有平台数据")

    platforms = Platform.objects.filter(is_active=True)

    if not platforms.exists():
        logger.warning("没有启用的平台需要同步")
        return {"success": True, "message": "没有启用的平台"}

    logger.info(f"找到 {platforms.count()} 个启用的平台")

    # 异步启动所有平台的同步任务（不等待结果）
    task_ids = []
    for platform in platforms:
        result = sync_all_platform_data.apply_async(args=[str(platform.id)])
        task_ids.append(
            {
                "platform_id": str(platform.id),
                "platform_name": platform.name,
                "task_id": result.id,
            }
        )

    logger.info(f"已为 {len(task_ids)} 个平台启动同步任务")

    return {
        "success": True,
        "total_platforms": len(platforms),
        "tasks": task_ids,
        "message": "所有平台同步任务已启动",
        "sync_time": timezone.now().isoformat(),
    }


@shared_task(
    bind=True,
    name="virt_center.collect_metrics",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
)
def collect_metrics(self, platform_id: str):
    """
    采集监控指标（主机和虚拟机）

    Args:
        platform_id: 平台ID
    """
    try:
        logger.info(f"开始采集监控指标: platform_id={platform_id}")

        # 获取平台对象
        try:
            platform = Platform.objects.get(id=platform_id, is_active=True)
        except Platform.DoesNotExist:
            logger.error(f"平台不存在或未启用: {platform_id}")
            return {"success": False, "error": "平台不存在或未启用"}

        # 连接 vSphere
        client = get_vsphere_client(platform)

        host_metrics_count = 0
        vm_metrics_count = 0
        current_time = timezone.now()

        try:
            with client:
                # 采集主机监控指标
                hosts = client.get_hosts()
                for host_vim in hosts:
                    try:
                        # 查找数据库中的主机
                        host_obj = Host.objects.filter(platform=platform, name=host_vim.name).first()
                        if not host_obj:
                            continue

                        summary = host_vim.summary
                        quick_stats = summary.quickStats if summary else None

                        if quick_stats:
                            HostMetrics.objects.create(
                                host=host_obj,
                                cpu_usage_percent=host_obj.cpu_usage,
                                cpu_usage_mhz=quick_stats.overallCpuUsage or 0,
                                memory_usage_percent=host_obj.memory_usage,
                                memory_used_mb=quick_stats.overallMemoryUsage or 0,
                                collected_at=current_time,
                            )
                            host_metrics_count += 1

                    except Exception as e:
                        logger.error(f"采集主机监控指标失败 {host_vim.name}: {str(e)}")
                        continue

                # 采集虚拟机监控指标
                vms = client.get_vms()
                for vm_vim in vms:
                    try:
                        # 跳过模板
                        if vm_vim.config and vm_vim.config.template:
                            continue

                        # 查找数据库中的虚拟机
                        vm_obj = VirtualMachine.objects.filter(platform=platform, name=vm_vim.name).first()
                        if not vm_obj:
                            continue

                        summary = vm_vim.summary
                        quick_stats = summary.quickStats if summary else None

                        if quick_stats:
                            VMMetrics.objects.create(
                                vm=vm_obj,
                                cpu_usage_percent=vm_obj.cpu_usage_percent,
                                memory_usage_percent=vm_obj.memory_usage_percent,
                                memory_used_mb=quick_stats.guestMemoryUsage or 0,
                                collected_at=current_time,
                            )
                            vm_metrics_count += 1

                    except Exception as e:
                        logger.error(f"采集虚拟机监控指标失败 {vm_vim.name}: {str(e)}")
                        continue

                logger.info(f"监控指标采集完成: 主机={host_metrics_count}, 虚拟机={vm_metrics_count}")

                return {
                    "success": True,
                    "platform_id": platform_id,
                    "host_metrics_count": host_metrics_count,
                    "vm_metrics_count": vm_metrics_count,
                    "collected_at": current_time.isoformat(),
                }

        except Exception as e:
            logger.error(f"连接或采集监控指标失败: {str(e)}")
            raise

    except Exception as exc:
        logger.error(f"采集监控指标失败: {str(exc)}")
        raise self.retry(exc=exc)


@shared_task(name="virt_center.collect_all_platforms_metrics")
@register_as_period_task(interval=300)  # 每5分钟采集一次监控指标
def collect_all_platforms_metrics():
    """定期采集所有平台的监控指标"""
    logger.info("开始定期采集所有平台监控指标")

    platforms = Platform.objects.filter(is_active=True)

    if not platforms.exists():
        logger.warning("没有启用的平台需要采集")
        return {"success": True, "message": "没有启用的平台"}

    logger.info(f"找到 {platforms.count()} 个启用的平台")

    # 异步启动所有平台的监控采集任务（不等待结果）
    task_ids = []
    for platform in platforms:
        result = collect_metrics.apply_async(args=[str(platform.id)])
        task_ids.append(
            {
                "platform_id": str(platform.id),
                "platform_name": platform.name,
                "task_id": result.id,
            }
        )

    logger.info(f"已为 {len(task_ids)} 个平台启动监控采集任务")

    return {
        "success": True,
        "total_platforms": len(platforms),
        "tasks": task_ids,
        "message": "所有平台监控采集任务已启动",
    }

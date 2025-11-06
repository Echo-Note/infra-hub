#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
vSphere 客户端连接管理
支持 vCenter 和 ESXi 连接
"""

import logging
import ssl
from typing import Optional

from pyVim import connect
from pyVmomi import vim

logger = logging.getLogger(__name__)


class VSphereClient:
    """vSphere 客户端封装类"""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 443,
        ssl_verify: bool = False,
    ):
        """
        初始化 vSphere 客户端

        Args:
            host: vCenter/ESXi 主机地址
            username: 登录用户名
            password: 登录密码
            port: 连接端口，默认 443
            ssl_verify: 是否验证 SSL 证书
        """
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.ssl_verify = ssl_verify
        self._service_instance: Optional[vim.ServiceInstance] = None
        self._session_manager: Optional[vim.SessionManager] = None

    def connect(self) -> vim.ServiceInstance:
        """
        连接到 vSphere

        Returns:
            vim.ServiceInstance: 服务实例对象

        Raises:
            vim.fault.InvalidLogin: 登录失败
            Exception: 连接失败
        """
        try:
            # 清理主机地址（移除协议前缀）
            host = self.host.replace("https://", "").replace("http://", "").strip("/")

            # 创建 SSL 上下文
            if not self.ssl_verify:
                # 禁用 SSL 验证（适用于自签名证书）
                context = ssl._create_unverified_context()
            else:
                # 启用 SSL 验证
                context = ssl.create_default_context()
                context.check_hostname = True
                context.verify_mode = ssl.CERT_REQUIRED

            # 连接到 vCenter/ESXi
            logger.info(f"正在连接到 vSphere: {host}:{self.port}")
            self._service_instance = connect.SmartConnect(
                host=host,
                user=self.username,
                pwd=self.password,
                port=self.port,
                sslContext=context,
            )

            if not self._service_instance:
                raise Exception("无法连接到 vSphere")

            # 获取会话管理器
            self._session_manager = self._service_instance.content.sessionManager

            logger.info(f"成功连接到 vSphere: {host}")
            return self._service_instance

        except vim.fault.InvalidLogin as e:
            logger.error(f"vSphere 登录失败: {e.msg}")
            raise
        except ssl.SSLError as e:
            logger.error(f"SSL 连接失败: {str(e)}")
            logger.info("提示：如果使用自签名证书，请设置 ssl_verify=False")
            raise
        except Exception as e:
            logger.error(f"连接 vSphere 失败: {str(e)}")
            raise

    def disconnect(self):
        """断开 vSphere 连接"""
        if self._service_instance:
            try:
                connect.Disconnect(self._service_instance)
                logger.info(f"已断开与 vSphere 的连接: {self.host}")
            except Exception as e:
                logger.warning(f"断开连接时出错: {str(e)}")
            finally:
                self._service_instance = None
                self._session_manager = None

    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        if not self._service_instance:
            return False
        try:
            # 尝试获取当前时间来验证连接
            self._service_instance.CurrentTime()
            return True
        except Exception:
            return False

    @property
    def service_instance(self) -> vim.ServiceInstance:
        """获取服务实例（自动连接）"""
        if not self.is_connected:
            self.connect()
        return self._service_instance

    @property
    def content(self) -> vim.ServiceInstanceContent:
        """获取服务内容"""
        return self.service_instance.RetrieveContent()

    def get_about_info(self) -> dict:
        """
        获取 vSphere 版本信息

        Returns:
            dict: 包含 vSphere 版本、构建号等信息
        """
        about = self.content.about
        return {
            "name": about.name,
            "full_name": about.fullName,
            "vendor": about.vendor,
            "version": about.version,
            "build": about.build,
            "api_type": about.apiType,
            "api_version": about.apiVersion,
            "instance_uuid": about.instanceUuid,
        }

    def get_datacenters(self) -> list[vim.Datacenter]:
        """
        获取所有数据中心

        Returns:
            list: 数据中心对象列表
        """
        container = self.content.rootFolder
        viewType = [vim.Datacenter]
        recursive = True
        containerView = self.content.viewManager.CreateContainerView(container, viewType, recursive)
        datacenters = containerView.view
        containerView.Destroy()
        return datacenters

    def get_clusters(self, datacenter: Optional[vim.Datacenter] = None) -> list[vim.ClusterComputeResource]:
        """
        获取集群列表

        Args:
            datacenter: 指定数据中心，为 None 则获取所有

        Returns:
            list: 集群对象列表
        """
        container = datacenter.hostFolder if datacenter else self.content.rootFolder
        viewType = [vim.ClusterComputeResource]
        recursive = True
        containerView = self.content.viewManager.CreateContainerView(container, viewType, recursive)
        clusters = containerView.view
        containerView.Destroy()
        return clusters

    def get_hosts(self, datacenter: Optional[vim.Datacenter] = None) -> list[vim.HostSystem]:
        """
        获取 ESXi 主机列表

        Args:
            datacenter: 指定数据中心，为 None 则获取所有

        Returns:
            list: ESXi 主机对象列表
        """
        container = datacenter.hostFolder if datacenter else self.content.rootFolder
        viewType = [vim.HostSystem]
        recursive = True
        containerView = self.content.viewManager.CreateContainerView(container, viewType, recursive)
        hosts = containerView.view
        containerView.Destroy()
        return hosts

    def get_vms(self, datacenter: Optional[vim.Datacenter] = None) -> list[vim.VirtualMachine]:
        """
        获取虚拟机列表

        Args:
            datacenter: 指定数据中心，为 None 则获取所有

        Returns:
            list: 虚拟机对象列表
        """
        container = datacenter.vmFolder if datacenter else self.content.rootFolder
        viewType = [vim.VirtualMachine]
        recursive = True
        containerView = self.content.viewManager.CreateContainerView(container, viewType, recursive)
        vms = containerView.view
        containerView.Destroy()
        return vms

    def get_datastores(self, datacenter: Optional[vim.Datacenter] = None) -> list[vim.Datastore]:
        """
        获取数据存储列表

        Args:
            datacenter: 指定数据中心，为 None 则获取所有

        Returns:
            list: 数据存储对象列表
        """
        container = datacenter.datastoreFolder if datacenter else self.content.rootFolder
        viewType = [vim.Datastore]
        recursive = True
        containerView = self.content.viewManager.CreateContainerView(container, viewType, recursive)
        datastores = containerView.view
        containerView.Destroy()
        return datastores

    def get_networks(self, datacenter: Optional[vim.Datacenter] = None) -> list:
        """
        获取网络列表

        Args:
            datacenter: 指定数据中心，为 None 则获取所有

        Returns:
            list: 网络对象列表
        """
        container = datacenter.networkFolder if datacenter else self.content.rootFolder
        viewType = [vim.Network]
        recursive = True
        containerView = self.content.viewManager.CreateContainerView(container, viewType, recursive)
        networks = containerView.view
        containerView.Destroy()
        return networks

    def get_host_by_mo_ref(self, mo_ref: str) -> Optional[vim.HostSystem]:
        """
        根据 ManagedObject Reference 获取主机对象

        Args:
            mo_ref: 主机的 ManagedObject Reference ID

        Returns:
            vim.HostSystem: 主机对象，未找到返回 None
        """
        hosts = self.get_hosts()
        for host in hosts:
            if str(host._moId) == mo_ref:
                return host
        return None

    def enter_maintenance_mode(
        self, host_mo_ref: str, timeout: int = 0, evacuate_powered_off_vms: bool = False
    ) -> dict:
        """
        使主机进入维护模式

        Args:
            host_mo_ref: 主机的 MO Reference ID
            timeout: 超时时间（秒），0 表示无限等待
            evacuate_powered_off_vms: 是否迁移已关闭的虚拟机

        Returns:
            dict: 操作结果

        Raises:
            Exception: 主机不存在或操作失败
        """
        host = self.get_host_by_mo_ref(host_mo_ref)
        if not host:
            raise Exception(f"未找到主机: {host_mo_ref}")

        logger.info(f"正在使主机 {host.name} 进入维护模式")

        try:
            task = host.EnterMaintenanceMode_Task(timeout=timeout, evacuatePoweredOffVms=evacuate_powered_off_vms)
            # 等待任务完成（维护模式操作可能较慢）
            import time

            while task.info.state in [vim.TaskInfo.State.running, vim.TaskInfo.State.queued]:
                time.sleep(2)

            if task.info.state == vim.TaskInfo.State.success:
                logger.info(f"主机 {host.name} 已进入维护模式")
                return {"success": True, "message": "主机已进入维护模式"}
            else:
                error_msg = task.info.error.msg if task.info.error else "未知错误"
                raise Exception(f"进入维护模式失败: {error_msg}")

        except Exception as e:
            logger.error(f"主机 {host.name} 进入维护模式失败: {str(e)}")
            raise

    def exit_maintenance_mode(self, host_mo_ref: str, timeout: int = 0) -> dict:
        """
        使主机退出维护模式

        Args:
            host_mo_ref: 主机的 MO Reference ID
            timeout: 超时时间（秒），0 表示无限等待

        Returns:
            dict: 操作结果

        Raises:
            Exception: 主机不存在或操作失败
        """
        host = self.get_host_by_mo_ref(host_mo_ref)
        if not host:
            raise Exception(f"未找到主机: {host_mo_ref}")

        logger.info(f"正在使主机 {host.name} 退出维护模式")

        try:
            task = host.ExitMaintenanceMode_Task(timeout=timeout)
            # 等待任务完成
            import time

            while task.info.state in [vim.TaskInfo.State.running, vim.TaskInfo.State.queued]:
                time.sleep(2)

            if task.info.state == vim.TaskInfo.State.success:
                logger.info(f"主机 {host.name} 已退出维护模式")
                return {"success": True, "message": "主机已退出维护模式"}
            else:
                error_msg = task.info.error.msg if task.info.error else "未知错误"
                raise Exception(f"退出维护模式失败: {error_msg}")

        except Exception as e:
            logger.error(f"主机 {host.name} 退出维护模式失败: {str(e)}")
            raise

    def reboot_host(self, host_mo_ref: str, force: bool = False) -> dict:
        """
        重启主机

        Args:
            host_mo_ref: 主机的 MO Reference ID
            force: 是否强制重启

        Returns:
            dict: 操作结果

        Raises:
            Exception: 主机不存在或操作失败
        """
        host = self.get_host_by_mo_ref(host_mo_ref)
        if not host:
            raise Exception(f"未找到主机: {host_mo_ref}")

        logger.info(f"正在重启主机 {host.name}，force={force}")

        try:
            task = host.RebootHost_Task(force=force)
            logger.info(f"主机 {host.name} 重启命令已发送")
            return {"success": True, "message": "主机重启命令已发送", "task_id": str(task)}

        except Exception as e:
            logger.error(f"主机 {host.name} 重启失败: {str(e)}")
            raise

    def shutdown_host(self, host_mo_ref: str, force: bool = False) -> dict:
        """
        关闭主机

        Args:
            host_mo_ref: 主机的 MO Reference ID
            force: 是否强制关闭

        Returns:
            dict: 操作结果

        Raises:
            Exception: 主机不存在或操作失败
        """
        host = self.get_host_by_mo_ref(host_mo_ref)
        if not host:
            raise Exception(f"未找到主机: {host_mo_ref}")

        logger.info(f"正在关闭主机 {host.name}，force={force}")

        try:
            task = host.ShutdownHost_Task(force=force)
            logger.info(f"主机 {host.name} 关闭命令已发送")
            return {"success": True, "message": "主机关闭命令已发送", "task_id": str(task)}

        except Exception as e:
            logger.error(f"主机 {host.name} 关闭失败: {str(e)}")
            raise

    '''
    def wait_for_task(self, task: vim.Task, timeout: int = 300) -> bool:
        """
        等待任务完成

        Args:
            task: vSphere 任务对象
            timeout: 超时时间（秒），默认 300 秒

        Returns:
            bool: 任务是否成功完成

        Raises:
            Exception: 任务失败或超时
        """
        import time

        start_time = time.time()
        while task.info.state in [vim.TaskInfo.State.running, vim.TaskInfo.State.queued]:
            if time.time() - start_time > timeout:
                raise Exception(f"任务超时: {task.info.name}")
            time.sleep(1)

        if task.info.state == vim.TaskInfo.State.success:
            logger.info(f"任务完成: {task.info.name}")
            return True
        else:
            error_msg = task.info.error.msg if task.info.error else "未知错误"
            raise Exception(f"任务失败: {task.info.name} - {error_msg}")

    def find_vm_by_name(self, vm_name: str) -> Optional[vim.VirtualMachine]:
        """
        根据名称查找虚拟机

        Args:
            vm_name: 虚拟机名称

        Returns:
            vim.VirtualMachine: 虚拟机对象，未找到返回 None
        """
        vms = self.get_vms()
        for vm in vms:
            if vm.name == vm_name:
                return vm
        return None

    def find_vm_by_uuid(self, uuid: str, instance_uuid: bool = False) -> Optional[vim.VirtualMachine]:
        """
        根据 UUID 查找虚拟机

        Args:
            uuid: 虚拟机 UUID
            instance_uuid: 是否使用 instance UUID（True）还是 BIOS UUID（False）

        Returns:
            vim.VirtualMachine: 虚拟机对象，未找到返回 None
        """
        search_index = self.content.searchIndex
        vm = search_index.FindByUuid(None, uuid, True, instance_uuid)
        return vm

    def power_on_vm(self, vm: vim.VirtualMachine, wait: bool = True) -> vim.Task:
        """
        开启虚拟机电源

        Args:
            vm: 虚拟机对象
            wait: 是否等待任务完成

        Returns:
            vim.Task: 任务对象

        Raises:
            Exception: 虚拟机已处于开启状态或操作失败
        """
        if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOn:
            logger.warning(f"虚拟机 {vm.name} 已处于开启状态")
            return None

        logger.info(f"正在开启虚拟机: {vm.name}")
        task = vm.PowerOnVM_Task()
        if wait:
            self.wait_for_task(task)
            logger.info(f"虚拟机 {vm.name} 已成功开启")
        return task

    def power_off_vm(self, vm: vim.VirtualMachine, wait: bool = True) -> vim.Task:
        """
        关闭虚拟机电源（硬关机）

        Args:
            vm: 虚拟机对象
            wait: 是否等待任务完成

        Returns:
            vim.Task: 任务对象

        Raises:
            Exception: 虚拟机已处于关闭状态或操作失败
        """
        if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOff:
            logger.warning(f"虚拟机 {vm.name} 已处于关闭状态")
            return None

        logger.info(f"正在关闭虚拟机: {vm.name}")
        task = vm.PowerOffVM_Task()
        if wait:
            self.wait_for_task(task)
            logger.info(f"虚拟机 {vm.name} 已成功关闭")
        return task

    def shutdown_vm_guest(self, vm: vim.VirtualMachine) -> None:
        """
        通过 Guest OS 优雅关闭虚拟机（需要安装 VMware Tools）

        Args:
            vm: 虚拟机对象

        Raises:
            Exception: 虚拟机工具未运行或操作失败
        """
        if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOff:
            logger.warning(f"虚拟机 {vm.name} 已处于关闭状态")
            return

        if vm.guest.toolsRunningStatus != "guestToolsRunning":
            raise Exception(f"虚拟机 {vm.name} 的 VMware Tools 未运行，无法优雅关机")

        logger.info(f"正在优雅关闭虚拟机: {vm.name}")
        vm.ShutdownGuest()
        logger.info(f"已向虚拟机 {vm.name} 发送关机信号")

    def reboot_vm_guest(self, vm: vim.VirtualMachine) -> None:
        """
        通过 Guest OS 重启虚拟机（需要安装 VMware Tools）

        Args:
            vm: 虚拟机对象

        Raises:
            Exception: 虚拟机工具未运行或操作失败
        """
        if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOff:
            raise Exception(f"虚拟机 {vm.name} 处于关闭状态，无法重启")

        if vm.guest.toolsRunningStatus != "guestToolsRunning":
            raise Exception(f"虚拟机 {vm.name} 的 VMware Tools 未运行，无法优雅重启")

        logger.info(f"正在重启虚拟机: {vm.name}")
        vm.RebootGuest()
        logger.info(f"已向虚拟机 {vm.name} 发送重启信号")

    def delete_vm(
        self,
        vm: vim.VirtualMachine,
        force: bool = False,
        wait: bool = True,
    ) -> vim.Task:
        """
        删除虚拟机

        Args:
            vm: 虚拟机对象
            force: 是否强制删除（自动关闭电源）
            wait: 是否等待任务完成

        Returns:
            vim.Task: 任务对象

        Raises:
            Exception: 虚拟机处于开启状态且未设置 force，或删除失败
        """
        vm_name = vm.name
        power_state = vm.runtime.powerState

        # 检查虚拟机电源状态
        if power_state == vim.VirtualMachinePowerState.poweredOn:
            if not force:
                raise Exception(f"虚拟机 {vm_name} 处于开启状态，请先关闭或设置 force=True")
            else:
                logger.info(f"虚拟机 {vm_name} 处于开启状态，正在强制关闭...")
                self.power_off_vm(vm, wait=True)

        # 删除虚拟机
        logger.info(f"正在删除虚拟机: {vm_name}")
        task = vm.Destroy_Task()
        if wait:
            self.wait_for_task(task)
            logger.info(f"虚拟机 {vm_name} 已成功删除")
        return task

    def create_vm(
        self,
        vm_name: str,
        datacenter: vim.Datacenter,
        resource_pool: vim.ResourcePool,
        datastore: vim.Datastore,
        memory_mb: int = 1024,
        num_cpus: int = 1,
        guest_id: str = "otherGuest64",
        network: Optional[vim.Network] = None,
        disk_size_gb: int = 10,
        wait: bool = True,
    ) -> vim.Task:
        """
        创建新虚拟机

        Args:
            vm_name: 虚拟机名称
            datacenter: 数据中心对象
            resource_pool: 资源池对象
            datastore: 数据存储对象
            memory_mb: 内存大小（MB），默认 1024
            num_cpus: CPU 数量，默认 1
            guest_id: 客户机操作系统类型，默认 otherGuest64
            network: 网络对象（可选）
            disk_size_gb: 磁盘大小（GB），默认 10
            wait: 是否等待任务完成

        Returns:
            vim.Task: 任务对象

        Example:
            # 获取必要的对象
            datacenters = client.get_datacenters()
            datacenter = datacenters[0]

            clusters = client.get_clusters(datacenter)
            resource_pool = clusters[0].resourcePool

            datastores = client.get_datastores(datacenter)
            datastore = datastores[0]

            # 创建虚拟机
            task = client.create_vm(
                vm_name="test-vm",
                datacenter=datacenter,
                resource_pool=resource_pool,
                datastore=datastore,
                memory_mb=2048,
                num_cpus=2,
                disk_size_gb=20
            )
        """
        logger.info(f"正在创建虚拟机: {vm_name}")

        # 虚拟机文件路径
        vm_file_info = vim.vm.FileInfo()
        vm_file_info.vmPathName = f"[{datastore.name}]"

        # 虚拟机配置
        config = vim.vm.ConfigSpec()
        config.name = vm_name
        config.memoryMB = memory_mb
        config.numCPUs = num_cpus
        config.guestId = guest_id
        config.files = vm_file_info

        # 设备变更列表
        device_changes = []

        # 添加 SCSI 控制器
        scsi_spec = vim.vm.device.VirtualDeviceSpec()
        scsi_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        scsi_spec.device = vim.vm.device.ParaVirtualSCSIController()
        scsi_spec.device.key = 1000
        scsi_spec.device.deviceInfo = vim.Description()
        scsi_spec.device.deviceInfo.label = "SCSI Controller"
        scsi_spec.device.deviceInfo.summary = "SCSI Controller"
        scsi_spec.device.sharedBus = vim.vm.device.VirtualSCSIController.Sharing.noSharing
        device_changes.append(scsi_spec)

        # 添加磁盘
        disk_spec = vim.vm.device.VirtualDeviceSpec()
        disk_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        disk_spec.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
        disk_spec.device = vim.vm.device.VirtualDisk()
        disk_spec.device.key = 2000
        disk_spec.device.deviceInfo = vim.Description()
        disk_spec.device.deviceInfo.label = "Hard Disk 1"
        disk_spec.device.deviceInfo.summary = f"{disk_size_gb} GB"
        disk_spec.device.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
        disk_spec.device.backing.diskMode = "persistent"
        disk_spec.device.backing.fileName = f"[{datastore.name}] {vm_name}/{vm_name}.vmdk"
        disk_spec.device.capacityInKB = disk_size_gb * 1024 * 1024
        disk_spec.device.controllerKey = 1000
        disk_spec.device.unitNumber = 0
        device_changes.append(disk_spec)

        # 添加网络适配器（如果提供）
        if network:
            nic_spec = vim.vm.device.VirtualDeviceSpec()
            nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
            nic_spec.device = vim.vm.device.VirtualVmxnet3()
            nic_spec.device.key = 4000
            nic_spec.device.deviceInfo = vim.Description()
            nic_spec.device.deviceInfo.label = "Network Adapter 1"
            nic_spec.device.deviceInfo.summary = network.name
            nic_spec.device.backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
            nic_spec.device.backing.network = network
            nic_spec.device.backing.deviceName = network.name
            nic_spec.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
            nic_spec.device.connectable.startConnected = True
            nic_spec.device.connectable.allowGuestControl = True
            nic_spec.device.connectable.connected = True
            device_changes.append(nic_spec)

        config.deviceChange = device_changes

        # 创建虚拟机
        vm_folder = datacenter.vmFolder
        task = vm_folder.CreateVM_Task(config=config, pool=resource_pool)

        if wait:
            self.wait_for_task(task)
            logger.info(f"虚拟机 {vm_name} 创建成功")

        return task

    def clone_vm(
        self,
        source_vm: vim.VirtualMachine,
        clone_name: str,
        datacenter: vim.Datacenter,
        resource_pool: vim.ResourcePool,
        datastore: Optional[vim.Datastore] = None,
        power_on: bool = False,
        template: bool = False,
        wait: bool = True,
    ) -> vim.Task:
        """
        克隆虚拟机

        Args:
            source_vm: 源虚拟机对象
            clone_name: 克隆虚拟机名称
            datacenter: 数据中心对象
            resource_pool: 资源池对象
            datastore: 数据存储对象（可选，默认使用源虚拟机的数据存储）
            power_on: 克隆后是否自动开机
            template: 是否克隆为模板
            wait: 是否等待任务完成

        Returns:
            vim.Task: 任务对象

        Example:
            # 查找源虚拟机
            source_vm = client.find_vm_by_name("template-vm")

            # 获取必要的对象
            datacenters = client.get_datacenters()
            datacenter = datacenters[0]

            clusters = client.get_clusters(datacenter)
            resource_pool = clusters[0].resourcePool

            # 克隆虚拟机
            task = client.clone_vm(
                source_vm=source_vm,
                clone_name="cloned-vm",
                datacenter=datacenter,
                resource_pool=resource_pool,
                power_on=True
            )
        """
        logger.info(f"正在克隆虚拟机: {source_vm.name} -> {clone_name}")

        # 克隆规格
        clone_spec = vim.vm.CloneSpec()
        clone_spec.powerOn = power_on
        clone_spec.template = template

        # 位置规格
        relocate_spec = vim.vm.RelocateSpec()
        relocate_spec.pool = resource_pool

        if datastore:
            relocate_spec.datastore = datastore

        clone_spec.location = relocate_spec

        # 克隆虚拟机
        vm_folder = datacenter.vmFolder
        task = source_vm.CloneVM_Task(folder=vm_folder, name=clone_name, spec=clone_spec)

        if wait:
            self.wait_for_task(task)
            logger.info(f"虚拟机克隆成功: {clone_name}")

        return task
    '''

    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.disconnect()


def get_vsphere_client(platform) -> VSphereClient:
    """
    从平台对象创建 vSphere 客户端

    Args:
        platform: Platform 模型实例

    Returns:
        VSphereClient: vSphere 客户端实例

    Example:
        from apps.virt_center.models import Platform
        from apps.virt_center.services import get_vsphere_client

        platform = Platform.objects.get(name="生产环境 vCenter")
        client = get_vsphere_client(platform)

        # 使用上下文管理器自动连接和断开
        with client:
            about = client.get_about_info()
            print(about)

        # 或手动管理连接
        client.connect()
        try:
            vms = client.get_vms()
            for vm in vms:
                print(vm.name)
        finally:
            client.disconnect()
    """

    # 获取认证凭据
    credential = platform.credential

    return VSphereClient(
        host=platform.host,
        username=credential.username,
        password=credential.password,  # 会自动解密
        port=platform.port,
        ssl_verify=platform.ssl_verify,
    )


if __name__ == "__main__":
    # 测试连接示例
    # 注意：
    # 1. host 参数不要包含 https:// 前缀，只需要 IP 或域名
    # 2. 如果使用自签名证书，设置 ssl_verify=False

    import os

    client = VSphereClient(
        host=os.environ.get("vsphere_host"),  # 不要包含 https:// 前缀
        username=os.environ.get("vsphere_username"),  # 修改为你的用户名
        password=os.environ.get("vsphere_password"),  # 修改为你的密码
        port=443,
        ssl_verify=False,  # 自签名证书使用 False
    )

    try:
        with client:
            # 获取 vSphere 版本信息
            about = client.get_about_info()
            print("=" * 60)
            print("vSphere 连接成功！")
            print("=" * 60)
            print(f"名称: {about['name']}")
            print(f"完整名称: {about['full_name']}")
            print(f"版本: {about['version']}")
            print(f"构建号: {about['build']}")
            print(f"API 类型: {about['api_type']}")
            print(f"API 版本: {about['api_version']}")
            print("=" * 60)

            # 获取数据中心列表
            datacenters = client.get_datacenters()
            print(f"\n数据中心数量: {len(datacenters)}")
            for dc in datacenters:
                print(f"  - {dc.name}")

            # 获取主机列表
            hosts = client.get_hosts()
            print(f"\nESXi 主机数量: {len(hosts)}")
            for host in hosts[:5]:  # 只显示前5个
                print(f"  - {host.name}")

            # 获取虚拟机列表
            vms = client.get_vms()
            print(f"\n虚拟机数量: {len(vms)}")
            for vm in vms[:5]:  # 只显示前5个
                status = vm.runtime.powerState
                print(f"  - {vm.name} ({status})")

    except vim.fault.InvalidLogin as e:
        print(f"❌ 登录失败: 用户名或密码错误")
    except ssl.SSLError as e:
        print(f"❌ SSL 错误: {e}")
        print("💡 提示: 如果使用自签名证书，请设置 ssl_verify=False")
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("\n💡 检查清单:")
        print("  1. vCenter/ESXi 地址是否正确（不要包含 https:// 前缀）")
        print("  2. 网络是否可达（可以 ping 或 telnet 测试）")
        print("  3. 用户名密码是否正确")
        print("  4. 端口是否正确（默认 443）")
        print("  5. 如果使用自签名证书，ssl_verify 应设为 False")

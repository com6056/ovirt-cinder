# oVirt Storage Migration
# NFS -> Cinder/Ceph
# Author: Jordan Rodgers (com6056@gmail.com)
# Written for KGCOE at RIT (https://www.rit.edu/kgcoe/)

from ovirtsdk.api import API
from ovirtsdk.xml import params
from cinderclient.v1 import client
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
import os
import rados
import rbd
import time
import smtplib
import sys


def connect(ovirt_api_url, ovirt_username, ovirt_password, cinder_username, cinder_password, cinder_project,
            cinder_auth_url, ceph_conf_file, ceph_client, ceph_pool):
    VERSION = params.Version(major='4', minor='0')
    URL = ovirt_api_url
    USERNAME = ovirt_username
    PASSWORD = ovirt_password
    ovirt_api = API(url=URL, username=USERNAME, password=PASSWORD, insecure=True)

    cinder_api = client.Client(cinder_username, cinder_password, cinder_project, cinder_auth_url, service_type="volume")

    ceph_api = rados.Rados(conffile=ceph_conf_file, name="client.{}".format(ceph_client))
    ceph_api.connect()
    ceph_api_ioctx = ceph_api.open_ioctx(ceph_pool)

    return ovirt_api, cinder_api, ceph_api, ceph_api_ioctx


def get_vms_to_migrate(ovirt_api, search_query):
    vms_to_migrate = []
    for vm in ovirt_api.vms.list(query=search_query):
        print("'{}' is set to be migrated.".format(vm.name))
        vms_to_migrate.append(vm)
    return vms_to_migrate


def migrate_disks(ovirt_api, cinder_api, ceph_api_ioctx, vms_to_migrate, old_storage_id, new_storage_id, nfs_mount_dir,
                  migrate_tag, ceph_pool, ceph_client, ceph_conf_file):
    completed_vms = []
    failed_vms = []
    for vm in vms_to_migrate:
        print("Starting migration for '{}'.".format(vm.name))
        remove_snapshots(vm)
        print("[{}] Checking for disks to migrate...".format(vm.name))
        disks = vm.disks.list()
        for disk in disks:
            for storage_domain in disk.storage_domains.storage_domain:
                if storage_domain.id == old_storage_id:
                    print("[{}] '{}' needs to be migrated...".format(vm.name, disk.name))
                    try:
                        deactivate_disk(vm, disk)
                        print("[{}] Attempting to migrate '{}' to Cinder...".format(vm.name, disk.name))
                        cinder_disk_id = create_cinder_disk(cinder_api, disk, vm, cinder_volume_type)
                        delete_rbd(vm, disk, cinder_disk_id, ceph_api_ioctx)
                        print("[{}] Converting '{}' from NFS to RBD...".format(vm.name, disk.name))
                        image_path = find_image(old_storage_id, disk, nfs_mount_dir)
                        if image_path:
                            if os.system("qemu-img convert -p -O raw {} rbd:{}/volume-{}:id={}:conf={}".format(
                                              image_path, cinder_disk_id, ceph_pool, ceph_client, ceph_conf_file)) == 0:
                                new_disk = register_disk(vm, disk, ovirt_api, disk.name, new_storage_id)
                                if new_disk:
                                    attach_detach_disk(vm, disk, new_disk)
                                    set_boot_order(vm)
                                    print("[{}] Sucessfully migrated '{}'!".format(vm.name, disk.name))
                                else:
                                    print("[{}] Could not register the Cinder volume in oVirt.".format(vm.name))
                                    error_message(vm, disk, failed_vms)
                            else:
                                print("[{}] Failed to convert '{}' from NFS to RBD.".format(vm.name, disk.name))
                                error_message(vm, disk, failed_vms)

                        else:
                            print("[{}] Could not find the correct image file to convert.".format(vm.name))
                            error_message(vm, disk, failed_vms)
                    except:
                        error_message(vm, disk, failed_vms)
        done = check_vm(vm, old_storage_id)
        if done:
            remove_tag(vm, completed_vms, migrate_tag)
    return completed_vms, failed_vms


def remove_snapshots(vm):
    print("[{}] Checking VM for snapshots...".format(vm.name))
    snapshots = vm.snapshots.list()
    if len(snapshots) > 1:
        removed_snaps = 0
        for snapshot in snapshots:
            if snapshot.description != 'Active VM':
                print("[{}] Removing snapshot '{}'...".format(vm.name, snapshot.description))
                snapshot.delete()
                removed_snaps += 1
                new_snapshots = vm.snapshots.list()
                while len(new_snapshots) > len(snapshots) - removed_snaps:
                    time.sleep(3)
                    new_snapshots = vm.snapshots.list()


def deactivate_disk(vm, disk):
    print("[{}] Deactivating '{}' for migration...".format(vm.name, disk.name))
    if disk.active:
        disk.deactivate()
        while not disk.active:
            time.sleep(3)


def create_cinder_disk(cinder_api, disk, vm, cinder_volume_type):
    print("[{}] Creating a Cinder volume for {}...".format(vm.name, disk.name))
    new_disk = cinder_api.volumes.create(display_name=disk.name, size=disk.provisioned_size / 1073741824,
                                         volume_type=cinder_volume_type)
    disk_id = new_disk.id
    new_disk = cinder_api.volumes.get(disk_id)
    print("[{}] Waiting for the volume to be created...".format(vm.name))
    while new_disk.status != 'available':
        time.sleep(3)
        new_disk = cinder_api.volumes.get(disk_id)
    return disk_id


def delete_rbd(vm, disk, cinder_disk_id, ceph_api_ioctx):
    print("[{}] Deleting the underlying RBD for the new '{}' Cinder volume...".format(vm.name, disk.name))
    rbd_inst = rbd.RBD()
    rbd_name = "volume-{}".format(cinder_disk_id)
    rbd_inst.remove(ceph_api_ioctx, rbd_name)


def find_image(old_storage_id, disk, nfs_mount_dir):
    image_path = "{}/{}/images/{}/".format(nfs_mount_dir, old_storage_id, disk.id)
    image_dir_files = os.listdir(image_path)
    if len(image_dir_files) == 3:
        for filename in image_dir_files:
            if '.meta' in filename or '.lease' in filename:
                pass
            else:
                image_path += filename
                return image_path
    return False


def register_disk(vm, disk, ovirt_api, old_disk_name, new_storage_id):
    print("[{}] Registering '{}' in oVirt...".format(vm.name, disk.name))
    new_storage = ovirt_api.storagedomains.get(id=new_storage_id)
    unregistered_disks = new_storage.disks.list(unregistered=True)
    if len(unregistered_disks) == 1:
        if unregistered_disks[0].name == old_disk_name:
            new_disk = new_storage.disks.add(unregistered_disks[0], unregistered=True)
            return new_disk
    elif len(unregistered_disks) > 1:
        for disk in unregistered_disks:
            if disk.name == old_disk_name:
                new_disk = new_storage.disks.add(unregistered_disks[0], unregistered=True)
                return new_disk
    return False


def attach_detach_disk(vm, disk, new_disk):
    print("[{}] Attaching the '{}' Cinder volume to the VM...".format(vm.name, disk.name))
    vm.disks.add(params.Disk(id=new_disk.id, active=True))
    print("[{}] Detaching the '{}' NFS volume from the VM...".format(vm.name, disk.name))
    disk.delete(action=params.Action(detach=True))


def set_boot_order(vm):
    vm.set_os(params.OperatingSystem(boot=[params.Boot(dev='hd')]))
    vm.update()


def check_vm(vm, old_storage_id):
    disks = vm.disks.list()
    for disk in disks:
        for storage_domain in disk.storage_domains.storage_domain:
            if storage_domain.id == old_storage_id:
                return False
    return True


def remove_tag(vm, completed_vms, migrate_tag):
    completed_vms.append(vm.name)
    for tag in vm.tags.list():
        if tag.name == migrate_tag:
            tag.delete()


def error_message(vm, disk, failed_vms):
    failed_vms.append("{} ({})".format(vm.name, disk.name))
    print("[{}] ERROR: Could not migrate '{}'. Reactivating original disk. "
          "Please manually clean up any remnants from this failed migration.".format(vm.name, disk.name))
    disk.activate()


def email_report(completed_vms, failed_vms, mail_from, mail_to, mail_subject, mail_smtp_server):
    sender = mail_from
    receivers = mail_to
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = receivers
    msg['Subject'] = mail_subject

    body = "Successful VM Migrations:\n" \
           "{}\n\n" \
           "Failed VM Migrations:\n" \
           "{}".format("\n".join(completed_vms), "\n".join(failed_vms))
    msg.attach(MIMEText(body, 'plain'))
    text = msg.as_string()
    if completed_vms or failed_vms:
        try:
            server = smtplib.SMTP(mail_smtp_server, 25)
            server.starttls()
            server.sendmail(sender, receivers, text)
            server.quit()
            print("Successfully sent email report.")
        except:
            print("ERROR: Unable to send email report!")


if __name__ == "__main__":
    if os.path.isfile('.ovirt_migration_lock'):
        sys.exit("Lockfile exists. Exiting.")
    else:
        open('.ovirt_migration_lock', 'a').close()

    ovirt_api_url = 'https://ovirt.example.com/ovirt-engine/api/'
    ovirt_username = ''
    ovirt_password = ''
    cinder_username = 'admin'
    cinder_password = ''
    cinder_project = 'admin'
    cinder_auth_url = 'http://IP_OF_CINDER:35357/v2.0'
    ceph_conf_file = '/etc/ceph/ceph.conf'
    ceph_client = 'admin'

    ceph_pool = 'rbd'
    old_storage_id = ''
    new_storage_id = ''
    nfs_mount_dir = ''
    migrate_tag = 'Migrate_to_Cinder'
    cinder_volume_type = ''
    search_query = 'Storage.name= Status=down Tag={}'.format(migrate_tag)

    mail_from = ''
    mail_to = ''
    mail_subject = 'oVirt Cinder Migration Report'
    mail_smtp_server = ''

    ovirt_api, cinder_api, ceph_api, ceph_api_ioctx = connect(ovirt_api_url, ovirt_username, ovirt_password,
                                                              cinder_username, cinder_password, cinder_project,
                                                              cinder_auth_url, ceph_conf_file, ceph_client, ceph_pool)
    vms_to_migrate = get_vms_to_migrate(ovirt_api, search_query)
    completed_vms, failed_vms = migrate_disks(ovirt_api, cinder_api, ceph_api_ioctx, vms_to_migrate, old_storage_id,
                                              new_storage_id, nfs_mount_dir, migrate_tag, ceph_pool, ceph_client,
                                              ceph_conf_file)
    print("No more VMs to migrate.")
    email_report(completed_vms, failed_vms, mail_from, mail_to, mail_subject, mail_smtp_server)
    ceph_api_ioctx.close()
    ceph_api.shutdown()
    os.remove('.ovirt_migration_lock')

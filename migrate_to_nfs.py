# oVirt Storage Migration
# Cinder/Ceph -> NFS
# Author: Jordan Rodgers (com6056@gmail.com)
# Written for KGCOE at RIT (https://www.rit.edu/kgcoe/)

from ovirtsdk.api import API
from ovirtsdk.xml import params
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
import os
import time
import smtplib
import sys


def connect(ovirt_api_url, ovirt_username, ovirt_password):
    VERSION = params.Version(major='4', minor='0')
    ovirt_api = API(url=ovirt_api_url, username=ovirt_username, password=ovirt_password, insecure=True)

    return ovirt_api


def get_vms_to_migrate(ovirt_api, search_query):
    vms_to_migrate = []
    for vm in ovirt_api.vms.list(query=search_query):
        print("'{}' is set to be migrated.".format(vm.name))
        vms_to_migrate.append(vm)
    return vms_to_migrate


def migrate_disks(ovirt_api, vms_to_migrate, old_storage_id, new_storage_id, nfs_mount_dir, migrate_tag, ceph_pool,
                  ceph_client, ceph_conf_file):
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
                        print("[{}] Attempting to migrate '{}' to NFS...".format(vm.name, disk.name))
                        new_disk = create_nfs_disk(ovirt_api, new_storage_id, disk, vm)
                        print("[{}] Converting '{}' from RBD to NFS...".format(vm.name, disk.name))
                        image_path = find_image(new_storage_id, new_disk, nfs_mount_dir)
                        if image_path:
                            if os.system("qemu-img convert -p -O raw rbd:{}/volume-{}:id={}:conf={} {}".format(
                                                     ceph_pool, disk.id, ceph_client, ceph_conf_file, image_path)) == 0:
                                attach_detach_disk(vm, disk, new_disk)
                                set_boot_order(vm)
                                print("[{}] Sucessfully migrated '{}'!".format(vm.name, disk.name))
                            else:
                                print("[{}] Failed to convert '{}' from RBD to NFS.".format(vm.name, disk.name))
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


def create_nfs_disk(ovirt_api, new_storage_id, disk, vm):
    print("[{}] Creating an NFS image for '{}'...".format(vm.name, disk.name))
    new_storage_domain = ovirt_api.storagedomains.get(id=new_storage_id)
    disk_params = params.Disk()
    disk_params.set_alias(disk.name)
    disk_params.set_size(disk.size)
    disk_params.set_interface('virtio')
    disk_params.set_format('raw')
    new_disk = new_storage_domain.disks.add(disk_params)
    while new_disk.status.state != 'ok':
        new_disk = new_storage_domain.disks.get(id=new_disk.id)
        time.sleep(3)
    return new_disk


def find_image(new_storage_id, new_disk, nfs_mount_dir):
    image_path = "{}/{}/images/{}/".format(nfs_mount_dir, new_storage_id, new_disk.id)
    image_dir_files = os.listdir(image_path)
    if len(image_dir_files) == 3:
        for filename in image_dir_files:
            if '.meta' in filename or '.lease' in filename:
                pass
            else:
                image_path += filename
                return image_path
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
    ceph_conf_file = '/etc/ceph/ceph.conf'
    ceph_client = 'admin'

    ceph_pool = 'rbd'
    old_storage_id = ''
    new_storage_id = ''
    nfs_mount_dir = ''
    migrate_tag = 'Migrate_to_NFS'
    cinder_volume_type = ''
    search_query = 'Storage.name= Status=down Tag={}'.format(migrate_tag)

    mail_from = ''
    mail_to = ''
    mail_subject = 'oVirt NFS Migration Report'
    mail_smtp_server = ''

    ovirt_api = connect(ovirt_api_url, ovirt_username, ovirt_password)
    vms_to_migrate = get_vms_to_migrate(ovirt_api, search_query)
    completed_vms, failed_vms = migrate_disks(ovirt_api, vms_to_migrate, old_storage_id, new_storage_id, nfs_mount_dir,
                                              migrate_tag, ceph_pool, ceph_client, ceph_conf_file)
    print("No more VMs to migrate.")
    email_report(completed_vms, failed_vms, mail_from, mail_to, mail_subject, mail_smtp_server)
    os.remove('.ovirt_migration_lock')

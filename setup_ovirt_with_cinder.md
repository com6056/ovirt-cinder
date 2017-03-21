This guide was originally written for KGCOE at RIT (https://www.rit.edu/kgcoe/).


This can be used to set up Kolla (Newton) on RHEL 7.
Kolla will fully set up Cinder so that it can be used to connect oVirt with Ceph for storage.
Kolla uses Docker containers to run each individual service required for Cinder to run.
Info on multiple backends can be found here: https://wiki.openstack.org/wiki/Cinder-multi-backend

Install dependencies:

```
yum -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm
yum -y install python-pip ntp python-devel libffi-devel openssl-devel gcc git ansible
```

Ensure pip is up to date and install the OpenStack/Docker clients:

```
pip install -U pip
pip install -U python-openstackclient python-neutronclient docker-py
```

Install Docker and configure it for Kolla:

```
curl -sSL https://get.docker.io | bash

mkdir -p /etc/systemd/system/docker.service.d

tee /etc/systemd/system/docker.service.d/kolla.conf <<-'EOF'
[Service]
MountFlags=shared
EOF

systemctl daemon-reload
systemctl restart docker
systemctl enable docker
```

Setup NTP and ensure libvirt isn't enabled/running:

```
systemctl enable ntpd.service
systemctl start ntpd.service
systemctl stop libvirtd.service
systemctl disable libvirtd.service
```

Install Kolla:
```
git clone https://git.openstack.org/openstack/kolla
cd kolla
git checkout stable/newton
cd ..
pip install kolla/
cd kolla
cp -r etc/kolla /etc/
```

Define the oVirt profile for the Kolla container build process:

```
tee /etc/kolla/kolla-build.conf <<-'EOF'
[profiles]
ovirt = cinder,data,keystone,mariadb,rabbitmq,rsyslog
EOF
```

Build the required containers defined in the previous step (this may take a while to run so run it in a tmux/screen session to ensure it isn't interrupted):

```
kolla-build -p ovirt
```

You should get something similar to the following output when it is finished (you can ignore the Congress images failing to build):

```
INFO:kolla.image.build:=========================
INFO:kolla.image.build:Successfully built images
INFO:kolla.image.build:=========================
INFO:kolla.image.build:keystone-fernet
INFO:kolla.image.build:mariadb
INFO:kolla.image.build:cinder-backup
INFO:kolla.image.build:neutron-base
INFO:kolla.image.build:cinder-api
INFO:kolla.image.build:cinder-volume
INFO:kolla.image.build:cinder-base
INFO:kolla.image.build:barbican-keystone-listener
INFO:kolla.image.build:rabbitmq
INFO:kolla.image.build:neutron-metadata-agent
INFO:kolla.image.build:openstack-base
INFO:kolla.image.build:base
INFO:kolla.image.build:keystone
INFO:kolla.image.build:keystone-ssh
INFO:kolla.image.build:barbican-base
INFO:kolla.image.build:cinder-rpcbind
INFO:kolla.image.build:cinder-scheduler
INFO:kolla.image.build:keystone-base
INFO:kolla.image.build:===========================
INFO:kolla.image.build:Images that failed to build
INFO:kolla.image.build:===========================
ERROR:kolla.image.build:congress-datasource Failed with status: matched
ERROR:kolla.image.build:congress-base Failed with status: error
```

Auto-generate the Kolla passwords (you can check them in /etc/kolla/passwords.yml):

```
kolla-genpwd
```

Tell Kolla which services to actually enable:

```
tee -a /etc/kolla/globals.yml <<-'EOF'

# Disable everything but Cinder
enable_cinder: "yes"
cinder_backend_ceph: "yes"
cinder_volume_driver: "ceph"
enable_glance: "no"
enable_heat: "no"
enable_magnum: "no"
enable_haproxy: "no"
enable_elk: "no"
enable_memcached: "no"
enable_swift: "no"
enable_nova: "no"
enable_neutron: "no"
enable_horizon: "no"
enable_murano: "no"
enable_ironic: "no"
enable_mistral: "no"
enable_ceph: "no"
enable_mongodb: "no"
EOF
```

Change "kolla_internal_vip_address" in /etc/kolla/globals.yml to the IP of the system you are deploying Kolla on and ensure "network_interface" in /etc/kolla/globals.yml matches the name of that interface.

Change the Docker version tag that Kolla will use in /etc/kolla/globals.yml (this version number may change so check https://hub.docker.com/r/kolla/centos-binary-heka/tags/ for the latest version tag to use for version 3):

```
sed -i 's|#openstack_release: "3.0.0"|openstack_release: "3.0.1"|g' /etc/kolla/globals.yml
```

Deploy containers (this may also take a while to run so run it in a tmux/screen session to ensure it isn't interrupted):

```
kolla-ansible deploy
```

If all went well, you should get something similar to the following as the final output:

```
PLAY RECAP *********************************************************************
localhost                  : ok=141  changed=45   unreachable=0    failed=0
```

Put your Ceph config into /etc/ceph/ceph.conf and /etc/kolla/cinder-volume/ceph.conf on the system running kolla.

On one of the systems in the Ceph cluster, run the following to generate a key for cinder:

```
ceph auth get-or-create client.cinder mon 'allow r' osd 'allow class-read object_prefix rbd_children, allow rwx pool=ENTER_POOL_NAME_HERE'
ENTER_POOL_NAME_HERE should be replaced with the pool you are using for oVirt/Cinder
```

Copy the key it outputs to /etc/ceph/ceph.client.cinder.keyring and /etc/kolla/cinder-volume/ceph.client.cinder.keyring on the system running Kolla.  
***If the Ceph user you created for Cinder isn't "cinder", then change the name of that file accordingly. For example, if you named the user "rit-cinder", then both files should be named "ceph.client.rit-cinder.keyring".***

Add the following line to the bottom of the "[DEFAULT]" section in /etc/kolla/cinder-volume/cinder.conf:

```
#For a single backend
enabled_backends = rbd-1
#For multiple backends
enabled_backends = rbd-1,rbd-2
```

Add the following to the bottom of the /etc/kolla/cinder-volume/cinder.conf file and replace the value for rbd_user with the name of the Ceph user you created for Cinder, replace the value for rbd_pool with the name of the Ceph pool you want to use for Cinder, and generate a UUID (the 'uuidgen' command works) to replace the one given here:

```
[rbd-1]
rbd_ceph_conf = /etc/ceph/ceph.conf
rbd_user = cinder
backend_host = rbd:volumes
rbd_pool = rbd
volume_backend_name = ceph
volume_driver = cinder.volume.drivers.rbd.RBDDriver
rbd_secret_uuid = 060e9922-baa1-4631-98d1-81272736e938

#If there is a second backend
[rbd-2]
rbd_ceph_conf = /etc/ceph/ceph.conf
rbd_user = cinder
backend_host = rbd:volumes
rbd_pool = rbd-2
volume_backend_name = ceph-2
volume_driver = cinder.volume.drivers.rbd.RBDDriver
rbd_secret_uuid = 060e9922-baa1-4631-98d1-81272736e938
```


Copy the cinder.conf file to the cinder-api config location:

```
cp /etc/kolla/cinder-volume/cinder.conf /etc/kolla/cinder-api/cinder.conf
```

Generate the admin credentials for Kolla and source them

```
kolla-ansible post-deploy
source /etc/kolla/admin-openrc.sh
```

Create the volume-type in cinder:

```
cinder type-create ceph
cinder type-key ceph set volume_backend_name=ceph
```

Reboot the system running Kolla

In oVirt, create a new external provider with the following information:  
Name: Whatever you want to name it (this will be the name of the storage domain that is created)  
Type: OpenStack Volume  
Provider URL: http://KOLLA_HOST:8776 (where KOLLA_HOST is either the IP address or hostname of the system running Kolla)  
Check "Requires Authentication"
Use the username, password, and tenant name from the /etc/kolla/admin-openrc.sh file that we sourced earlier  
For the "Authentication URL", use the OS_AUTH_URL that is in /etc/kolla/admin-openrc.sh, but change "v3" to "v2.0" at the end of the URL

Under the new external provider we created, go to the "Authentication Keys" tab and create a new one with the following:  
UUID: Copy the UUID from "rbd_secret_uuid" from the /etc/kolla/cinder-volume/cinder.conf on the system running Kolla  
Value: Use the key that you copied into /etc/ceph/ceph.client.cinder.keyring (only include the key itself)

You should now be able to create Cinder volumes to use with your VMs!
(You may need to update librados and other relevant Ceph packages on your oVirt hosts depending on which version of Ceph you are running.)

Here is a simple backup script that can be used to backup all of the Kolla and Cinder configurations (you need to install the MySQL or MariaDB client for this to work):

```
#!/bin/bash
SERVER=IP_OF_KOLLA_SERVER
USER=root
PASS=`cat /etc/kolla/passwords.yml | grep ^database_password | awk '{print $2}'`
BACKUP_DIR=DIRECTORY_TO_STORE_BACKUPS
DATE=`eval date +%Y%m%d`
DAYS_TO_KEEP=7

echo "Backing up MySQL databases..."
mysqldump --opt --databases cinder keystone -h$SERVER -u$USER -p$PASS | gzip > "${BACKUP_DIR}/mysql-$DATE.sql.gz"

echo "Backing up /etc/kolla and /etc/ceph..."
tar -C / -czf $BACKUP_DIR/kolla-$DATE.tgz etc/kolla
tar -C / -czf $BACKUP_DIR/ceph-$DATE.tgz etc/ceph

#Everything should be in /etc/kolla and /etc/ceph but we can back up the configs in the containers just in case

echo "Backing up configurations and logs in containers..."
docker exec cinder_volume tar -C / -czf /var/lib/cinder/cinder_volume-$DATE.tgz etc/cinder var/log/cinder var/lib/cinder --exclude=cinder_volume-$DATE.tgz
docker exec cinder_backup tar -C / -czf /var/lib/cinder/cinder_backup-$DATE.tgz etc/cinder var/log/cinder var/lib/cinder --exclude=cinder_backup-$DATE.tgz
docker exec cinder_scheduler tar -C / -czf /var/lib/cinder/cinder_scheduler-$DATE.tgz etc/cinder var/log/cinder var/lib/cinder --exclude=cinder_scheduler-$DATE.tgz
docker exec cinder_api tar -C / -czf /var/lib/cinder/cinder_api-$DATE.tgz etc/cinder var/log/cinder var/lib/cinder --exclude cinder_api-$DATE.tgz
docker exec keystone tar -C / -czf /var/lib/keystone/keystone-$DATE.tgz etc/keystone var/log/keystone var/lib/keystone --exclude=keystone-$DATE.tgz

docker cp cinder_volume:/var/lib/cinder/cinder_volume-$DATE.tgz $BACKUP_DIR/cinder_volume-$DATE.tgz
docker cp cinder_backup:/var/lib/cinder/cinder_backup-$DATE.tgz $BACKUP_DIR/cinder_backup-$DATE.tgz
docker cp cinder_scheduler:/var/lib/cinder/cinder_scheduler-$DATE.tgz $BACKUP_DIR/cinder_backup-$DATE.tgz
docker cp cinder_api:/var/lib/cinder/cinder_api-$DATE.tgz $BACKUP_DIR/cinder_api-$DATE.tgz
docker cp keystone:/var/lib/keystone/keystone-$DATE.tgz $BACKUP_DIR/keystone-$DATE.tgz

docker exec cinder_volume rm /var/lib/cinder/cinder_volume-$DATE.tgz
docker exec cinder_backup rm /var/lib/cinder/cinder_backup-$DATE.tgz
docker exec cinder_scheduler rm /var/lib/cinder/cinder_scheduler-$DATE.tgz
docker exec cinder_api rm /var/lib/cinder/cinder_api-$DATE.tgz
docker exec keystone rm /var/lib/keystone/keystone-$DATE.tgz

echo "Removing backups older than ${DAYS_TO_KEEP} days..."
find $BACKUP_DIR -mtime +$DAYS_TO_KEEP -type f -delete

echo "Backup completed!"
```

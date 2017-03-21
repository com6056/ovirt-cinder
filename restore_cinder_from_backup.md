This guide was originally written for KGCOE at RIT (https://www.rit.edu/kgcoe/).

This guide assumes that you are using the backup script provided in LINK TO SETUP GUIDE
Let's assume that the last available backup date was 20161012 and that you would like to restore from that backup.  
If you are asked to overwrite in any of the steps with the cp command, go ahead and overwrite everything.  
**All of the replacing of hostnames/IP addresses in this guide can be skipped if restoring to a system with the same hostname and IP address as the one that the backup was taken on.**

You must first follow this guide up to right before you run the "kolla-ansible deploy" command: LINK TO SETUP GUIDE

Run the following commands just to make sure that there are no containers or configurations that will interfere with the restoration process:

```
cd /root/kolla #(or wherever your kolla repo is that you cloned from git)
tools/cleanup-containers
tools/cleanup-host
```

Extract the backup archives for the Kolla and Ceph configurations and restore them:

```
cd /root/cinder_backups #(or wherever the directory is that holds the backups)
mkdir ceph && cd ceph && tar xvf ../ceph-20161012.tgz
cp * / -r
cd ..
mkdir kolla && cd kolla && tar xvf ../kolla-20161012.tgz
cp * / -r
```

If the system you are restoring to has a different IP address or hostname than the original system, replace all occurrences of them with the new one in the Kolla configuration files (otherwise, you can skip this step):

```
cd /etc/kolla
find . -type f -exec sed -i 's/ENTER_OLD_IP_HERE/ENTER_NEW_IP_HERE/g' {} +
find . -type f -exec sed -i 's/ENTER_OLD_HOSTNAME_HERE/ENTER_NEW_HOSTNAME_HERE/g' {} +
```

**For the hostname, make sure you replace all occurrences of it. For example, to change cinder-old.old.example.org to cinder-new.new.example.org for the Kolla configs and ensure that all occurrences are replaced:**

```
find . -type f -exec sed -i 's/cinder-old/cinder-new/g' {} +
find . -type f -exec sed -i 's/old.example.org/new.example.org/g' {} +
```

Modify these commands as necessary for your specific hostnames. Do a grep and check to make sure all of them were replaced properly just to be safe.

Deploy all of the Kolla containers (this may take a while to run so run it in a tmux/screen session to ensure it isn't interrupted):

```
kolla-ansible deploy
```

The container deployment process will overwrite some of the configuration files, so copy them over again and replace the old IP/hostname with the new one:

```
cd /root/cinder_backups/kolla #(or wherever the directory is that holds the Kolla backup)
cp * / -r
cd /etc/kolla
#These are only necessary if the system you are restoring to has a different hostname or IP address than the system the backup was from
find . -type f -exec sed -i 's/ENTER_OLD_IP_HERE/ENTER_NEW_IP_HERE/g' {} +
find . -type f -exec sed -i 's/ENTER_OLD_HOSTNAME_HERE/ENTER_NEW_HOSTNAME_HERE/g' {} +
```

**If you replace the hostname, make sure you replace all occurrences of it. For example, to change cinder-old.old.example.org to cinder-new.new.example.org for the Kolla configs and ensure that all occurrences are replaced:**

```
find . -type f -exec sed -i 's/cinder-old/cinder-new/g' {} +
find . -type f -exec sed -i 's/old.example.org/new.example.org/g' {} +
```

Modify these commands as necessary for your specific hostnames. Do a grep and check to make sure all of them were replaced properly just to be safe.  
**If anything relating to your Ceph configuration matches anything that you are replacing, make sure you check them and change them back manually. Some places to check are the following:**  
/etc/kolla/cinder-volume/cinder.conf (check rbd_user and rbd_pool if your user/pool is similar to the hostname of the system)
/etc/kolla/cinder-api/cinder.conf (check rbd_user and rbd_pool if your user/pool is similar to the hostname of the system)  
/etc/kolla/cinder-volume/ceph.conf  
/etc/kolla/cinder-volume/ceph.client.cinder.keyring (or whatever your keyring file is named) (make sure the name of the user at the top is correct)

Extract the database backup. Again, if the system you are restoring to has a different IP address or hostname than the original system, replace all occurrences of them with the new one in the database backup before restoring it:

```
cd /root/cinder_backups #(or wherever the directory is that holds the backups)
gzip -d mysql-20161012.sql.gz
#These are only necessary if the system you are restoring to has a different hostname or IP address than the system the backup was from
sed -i -e 's/ENTER_OLD_IP_HERE/ENTER_NEW_IP_HERE/g' mysql-20161012.sql
sed -i -e 's/ENTER_OLD_HOSTNAME_HERE/ENTER_NEW_HOSTNAME_HERE/g' mysql-20161012.sql
```

**If you replace the hostname, make sure you replace all occurrences of it. For example, to change cinder-old.old.example.org to cinder-new.new.example.org for the Kolla configs and ensure that all occurrences are replaced for the database:**

```
sed -i -e 's/cinder-old/cinder-new/g' mysql-20161012.sql
sed -i -e 's/old.example.org/new.example.org/g' mysql-20161012.sql
```

Modify these commands as necessary for your specific hostnames. Do a grep and check to make sure all of them were replaced properly just to be safe.

If you had to change the IP address in the above steps, in /etc/kolla/rabbitmq/rabbitmq.config make sure to change the periods separating each octet of the IP address to commas in the line that starts with "{inet_dist_use_interface,". For example:

```
{inet_dist_use_interface, {1.2.3.4}},
```

should be changed to

```
{inet_dist_use_interface, {1,2,3,4}},
```

Import the database into the new system:

```
yum -y install mysql
mysql -u root -p`cat /etc/kolla/passwords.yml | grep ^database_password | awk '{print $2}'` -h IP_OF_NEW_SYSTEM < mysql-20161012.sql
```

If you had applied any other custom configurations to the various services running in the containers, apply them now. MAKE SURE YOU CHANGE ANY IP ADDRESSES AND HOSTNAMES TO MATCH THE NEW SYSTEM.

Reboot the system and then change the IP addresses that oVirt uses to connect to Cinder with and you should be good to go!

Found some of the information used in this guide here: http://docs.openstack.org/ops-guide/ops-backup-recovery.html

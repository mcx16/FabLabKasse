#!/bin/bash
set -e
# provisioning for Debian / Ubuntu
echo "ONLY RUN THIS SCRIPT on a disposable VM or a PC specially for setting up kasse. It will change your xsession and uninstall some packages."
echo "press ctrl-c to exit, Enter to continue (will continue automatically under Vagrant provisioner)"
read

[ -d /home/vagrant ] && echo "Running under Vagrant, using the vagrant user" && RUNNING_IN_VAGRANT=true || RUNNING_IN_VAGRANT=false

# change to the git directory
if $RUNNING_IN_VAGRANT; then
    cd /vagrant
fi
if [ ! -f requirements.txt ]; then
    pwd
    ls -l
    echo "This script must be run in the FabLabKasse main git directory which contains requirements.txt"
    exit 1
fi


# ~~~~~~~~~~~~~~~
# Install dependencies for running and development
# This is the part that you will need when developing FablabKasse
# ~~~~~~~~~~~~~~~

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get -y install git
# Python3 stuff
sudo DEBIAN_FRONTEND=noninteractive apt-get -y install python3-pip python3 python3-dateutil python3-lxml python3-termcolor python3-serial python3-qrcode python3-docopt python3-requests python3-simplejson python3-sphinx
sudo DEBIAN_FRONTEND=noninteractive apt-get -y install python3-qtpy python3-pyqt5 pyqt5-dev-tools
sudo python3 -m pip install -r requirements.txt
# Dependencies only for Testing / Vagrant automation (dummy printserver / dummy FAUCard device)
sudo DEBIAN_FRONTEND=noninteractive apt-get -y install psmisc socat


# ~~~~~~~~~~~~~~~~
# Set up auto-start
# DO NOT run this on your standard PC, it will mess up your system configuration!
# Intended for use in a separate VM or on the real cash system.
# ~~~~~~~~~~~~~~~

# Graphical environment and styling
sudo DEBIAN_FRONTEND=noninteractive apt-get -y install xserver-xorg git nodm ssh x11-apps xterm breeze breeze-icon-theme fonts-roboto-fontface curl
# try to install xrandr command
sudo DEBIAN_FRONTEND=noninteractive apt-get -y install x11-xserver-utils || true

# Setup user and 'kiosk mode' desktop manager that autostarts FabLabKasse
$RUNNING_IN_VAGRANT && INSTALL_USER=vagrant || INSTALL_USER=kasse
(! $RUNNING_IN_VAGRANT && ! test -d /home/kasse ) && sudo adduser kasse --disabled-password # not used in Vagrant, but in real system

# some package installs lightdm; we don't want it.
sudo apt-get -y remove lightdm
echo "NODM_ENABLED=true" | sudo tee -a /etc/default/nodm
echo "NODM_USER=$INSTALL_USER" | sudo tee -a /etc/default/nodm
# modemmanager interferes with serial port devices:
sudo apt-get -y remove modemmanager

rm -f /home/$INSTALL_USER/.xsession
if $RUNNING_IN_VAGRANT; then
	[ -d /home/$INSTALL_USER/FabLabKasse ] || ln -s /vagrant /home/$INSTALL_USER/FabLabKasse
else
	[ -d /home/$INSTALL_USER/FabLabKasse ] || sudo -u $INSTALL_USER git clone --recursive https://github.com/fau-fablab/FabLabKasse /home/$INSTALL_USER/FabLabKasse
fi

if $RUNNING_IN_VAGRANT; then
    # In the Vagrant VM, the shared folder is not mounted immediately on power-up but with some delay.
    # Therefore, a symlink to xsession doesn't work.
    echo "while [ ! -f /home/$INSTALL_USER/FabLabKasse/FabLabKasse/scripts/xsession.sh ]; do sleep 1; echo Waiting for git repo; done; /home/$INSTALL_USER/FabLabKasse/FabLabKasse/scripts/xsession.sh" > /home/$INSTALL_USER/.xsession
else
    ln -s /home/$INSTALL_USER/FabLabKasse/FabLabKasse/scripts/xsession.sh /home/$INSTALL_USER/.xsession
fi

# For consistency with the target system, use a German locale. The code should also work in other locales but this is not yet tested.
echo 'de_DE.UTF-8 UTF-8' | sudo tee -a /etc/locale.gen
sudo locale-gen
locale -a

# allow shutdown/reboot for any user
sudo cp /home/$INSTALL_USER/FabLabKasse/FabLabKasse/tools/sudoers.d/kassenterm-reboot-shutdown /etc/sudoers.d/

# load example config if no config.ini exists
cd /home/$INSTALL_USER/FabLabKasse/ && sudo -u $INSTALL_USER ./run.py --example --only-load-config
echo "Warning: if no config exists, an example config will be installed. Please change it if you use this for a real system"
echo "Warning: For using it on a real system, cronjobs must be setup manually, please see INSTALLING.md"

sudo service nodm stop
sleep 2
sudo service nodm start

# append if no such line, similar to https://fai-project.org/doc/man/ainsl.html.
# Adapted from https://unix.stackexchange.com/questions/530537/more-elegant-approach-to-append-text-to-a-file-only-if-the-string-doesnt-exist/530722#530722
#
# first argument: what to append
# second argument: the file to append to
ainsl() {
  p="$1"; (grep -q "${p}" || echo "${p}" >&0) <>"$2"
}

ainsl "cd ~/FabLabKasse/FabLabKasse" "/home/$INSTALL_USER/.bashrc"
ainsl "alias kb=~/FabLabKasse/FabLabKasse/kassenbuch.py" "/home/$INSTALL_USER/.bash_aliases"
ainsl "alias enableServiceMode=~/FabLabKasse/FabLabKasse/enableServiceMode.sh" "/home/$INSTALL_USER/.bash_aliases"

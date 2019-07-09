install :

cd ~/domoticz/plugins

mkdir SSZone

sudo apt-get update

sudo apt-get install git

git clone https://github.com/Erwanweb/SSZone.git SSZone

cd SSZone

sudo chmod +x plugin.py

sudo /etc/init.d/domoticz.sh restart

Upgrade :

cd ~/domoticz/plugins/SSZone

git reset --hard

git pull --force

sudo chmod +x plugin.py

sudo /etc/init.d/domoticz.sh restart

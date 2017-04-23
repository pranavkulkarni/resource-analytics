import sys
import requests
import os
import json
import redis
import time

instance_sizes = [ "512mb", "1gb", "2gb", "4gb", "8gb", "16gb", "32gb", "48gb", "64gb"]
headers = {
  'Content-Type': 'application/json',
  'Authorization': 'Bearer ' + os.environ["DO_API_TOKEN"]
}
steady_state_instance_size = '' 
droplet_ids_map = dict()
redis = redis.Redis(
    host = '127.0.0.1',
    port = 6379)

################################################


def fetch_all_droplet_ids():
    global droplet_ids_map, steady_state_instance_size
    print('\nFetching droplet ids of checkbox.io app servers...\n');
    r = requests.get("https://api.digitalocean.com/v2/droplets/", headers = headers);
    for droplet in json.loads(r.text)['droplets']:
        #print droplet['id'], droplet['name'], droplet['size_slug'], droplet['networks']['v4'][0]['ip_address']
        if 'checkbox-io-prod' in droplet['name']:
            droplet_ids_map[droplet['networks']['v4'][0]['ip_address']] = droplet['id']
            if steady_state_instance_size == '':
                steady_state_instance_size = droplet['size_slug']
            

def resize(target_droplet_id, new_size):
    payload = { 'type': 'resize', 'size': new_size }
    r = requests.post("https://api.digitalocean.com/v2/droplets/" + str(target_droplet_id) + "/actions", headers = headers, json = payload);
    action_id = json.loads(r.text)['action']['id']
    while True:
        time.sleep(5);
        r = requests.get("https://api.digitalocean.com/v2/actions/" + str(action_id), headers = headers)
        if json.loads(r.text)['action']['status'] == 'completed':
            print "Droplet " + str(target_droplet_id) + " resized successfully."
            break


def upsize(new_size):
    target_server_ip = pop_server_redis()
    target_droplet_id = droplet_ids_map[target_server_ip]
    droplet_details = poweroff_server(target_droplet_id)
    
    resize(target_droplet_id, new_size)
    poweron_server(target_droplet_id)
    # TODO: CALL ansible playbook to restart services in order
    restart_services_server(target_server_ip)
    push_server_redis(target_server_ip)



def downsize(new_size):
    target_server_ip = pop_server_redis()
    target_droplet_id = droplet_ids_map[target_server_ip]
    droplet_details = poweroff_server(target_droplet_id)
    
    resize(target_droplet_id, new_size)
    poweron_server(target_droplet_id)
    # TODO: CALL ansible playbook to restart services in order
    restart_services_server(target_server_ip)
    push_server_redis(target_server_ip)


def pop_server_redis():
    return redis.lpop('prodServers')


def push_server_redis(target_server_ip):
    redis.rpush('prodServers', target_server_ip)


def poweroff_server(target_droplet_id):
    payload = { 'type': 'power_off' }
    r = requests.post("https://api.digitalocean.com/v2/droplets/" + str(target_droplet_id) + "/actions", headers = headers, json = payload);
    action_id = json.loads(r.text)['action']['id']
    while True:
        time.sleep(2);
        r = requests.get("https://api.digitalocean.com/v2/actions/" + str(action_id), headers = headers)
        if json.loads(r.text)['action']['status'] == 'completed':
            print "Droplet " + str(target_droplet_id) + " powered off successfully."
            break


def poweron_server(target_droplet_id):
    payload = { 'type': 'power_on' }
    r = requests.post("https://api.digitalocean.com/v2/droplets/" + str(target_droplet_id) + "/actions", headers = headers, json = payload);
    action_id = json.loads(r.text)['action']['id']
    while True:
        time.sleep(2);
        r = requests.get("https://api.digitalocean.com/v2/actions/" + str(action_id), headers = headers)
        if json.loads(r.text)['action']['status'] == 'completed':
            print "Droplet " + str(target_droplet_id) + " powered on successfully."
            break


def restart_services_server(target_server_ip):
    print "Restarting services on " + str(target_server_ip)
    

def collect_metrics():
    print "\nRunning experiments and collecting metrics.\n"
    

def email_report():
    print "\nSending email report.\n"
    

def main():
    if len(sys.argv) < 1 :
		print "Usage: python monkey.py"
		exit(1)
    print('\nResource Analytics Monkey : STARTED\n');
    global headers, redis
    fetch_all_droplet_ids()
    number_active_prod_servers = len(droplet_ids_map)
    if number_active_prod_servers == 1:
        print('\nResource Analytics Monkey : ABORTED - Only 1 active prod server is running!\n')
        exit(1)
    
    new_size = instance_sizes[instance_sizes.index(steady_state_instance_size) + 1] # TODO handle last index
    for i in range(number_active_prod_servers):
        upsize(new_size)

    collect_metrics()
    time.sleep(15)
    
    new_size = instance_sizes[instance_sizes.index(steady_state_instance_size) - 2] # TODO handle first index
    for i in range(number_active_prod_servers):
        downsize(new_size)

    collect_metrics()
    time.sleep(15)

    email_report()
    
    print('\nResource Analytics Monkey : COMPLETED\n');

if __name__ == '__main__':
    main()
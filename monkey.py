import sys
import requests
import os
import json
import redis
import time
import smtplib
from subprocess import call
import requests.packages.urllib3
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from tabulate import tabulate

requests.packages.urllib3.disable_warnings()
instance_sizes = [ "512mb", "1gb", "2gb", "4gb", "8gb", "16gb", "32gb", "48gb", "64gb"]
headers = { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ["DO_API_TOKEN"] }
steady_state_instance_size = '' 
droplet_ids_map = dict() # ip -> id
droplet_names_map = dict() # ip -> name
redis = redis.Redis(
    host = '127.0.0.1',
    port = 6379)
infrastructure_ip = ''
metrics_map = dict()
new_relic_api_key = os.environ["NEW_RELIC_API_KEY"]
header_new_relic = { 'X-Api-Key': new_relic_api_key }

################################################


def fetch_all_droplet_ids():
    global droplet_ids_map, droplet_names_map, steady_state_instance_size, infrastructure_ip
    print('\nFetching droplet ids of checkbox.io app servers...\n');
    r = requests.get("https://api.digitalocean.com/v2/droplets/", headers = headers);
    for droplet in json.loads(r.text)['droplets']:
        #print droplet['id'], droplet['name'], droplet['size_slug'], droplet['networks']['v4'][0]['ip_address']
        if 'checkbox-io-prod' in droplet['name']:
            droplet_ids_map[droplet['networks']['v4'][0]['ip_address']] = droplet['id']
            droplet_names_map[droplet['networks']['v4'][0]['ip_address']] = droplet['name']
            if steady_state_instance_size == '':
                steady_state_instance_size = droplet['size_slug']

        if 'checkbox-io-infrastructure-server' in droplet['name']:
            infrastructure_ip = droplet['networks']['v4'][0]['ip_address']
            

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
    time.sleep(45)
    restart_services_server(target_server_ip)
    push_server_redis(target_server_ip)

def downsize(new_size):
    target_server_ip = pop_server_redis()
    target_droplet_id = droplet_ids_map[target_server_ip]
    droplet_details = poweroff_server(target_droplet_id)
    resize(target_droplet_id, new_size)
    poweron_server(target_droplet_id)
    time.sleep(45)
    restart_services_server(target_server_ip)
    push_server_redis(target_server_ip)

def pop_server_redis():
    return redis.lpop('prodServers')

def push_server_redis(target_server_ip):
    redis.rpush('prodServers', target_server_ip)


def poweroff_server(target_droplet_id):
    payload = { 'type': 'power_off' }
    r = requests.post("https://api.digitalocean.com/v2/droplets/" + str(target_droplet_id) + "/actions", headers = headers, json = payload);
    print r.text
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
    call([ "ansible-playbook", "-i", "inventory", "restart-services-checkbox.io-prod-playbook.yml", "-e", "serverGroupName=" + droplet_names_map[target_server_ip] ])
    
    
def collect_metrics(instance_size):
    global metrics_map
    payload = { 'filter[name]' : 'Checkbox.io' }
    print "\nRunning experiments and collecting metrics.\n"
    r = requests.get("https://api.newrelic.com/v2/applications.json", headers = header_new_relic, json = payload);
    response_time = json.loads(r.text)["applications"][0]["application_summary"]["response_time"]
    throughput = json.loads(r.text)["applications"][0]["application_summary"]["throughput"]
    apdex_score = json.loads(r.text)["applications"][0]["application_summary"]["apdex_score"]
    metrics_map[instance_size] = {"APP RESPONSE TIME" : response_time, "APP THROUGHPUT": throughput}

    r = requests.get("https://api.newrelic.com/v2/key_transactions.json", headers = header_new_relic);
    for idx, key_transaction  in enumerate(json.loads(r.text)["key_transactions"]):
        transaction_name = key_transaction["name"]
        transaction_response_time = key_transaction["application_summary"]["response_time"]
        transaction_throughput= key_transaction["application_summary"]["throughput"]
        metrics_map[instance_size]["API " + str(idx + 1)] = transaction_name
        metrics_map[instance_size]["API " + str(idx + 1) + " RESPONSE TIME"] = transaction_response_time
        metrics_map[instance_size]["API " + str(idx + 1) + " THROUGHPUT"] = transaction_throughput
    
    print "---\n"
    print metrics_map
    
    
def email_report():
    print "\nSending email report.\n"
    fromaddr = os.environ["EMAIL_USERNAME"]
    toaddr = fromaddr
    msg = MIMEMultipart()
    msg['From'] = fromaddr
    msg['To'] = toaddr
    msg['Subject'] = "Resource Analytics Monkey - Report"
    body = "\n---------------------- Chaos Engineering Results ----------------------\n"
    headings = ["INSTANCE TYPE", "ENDPOINT", "AVG RESPONSE TIME"]
    data = []
    for key in metrics_map:
        for k in metrics_map[key]:
            data.append([key, k, str(metrics_map[key][k])])
    body += tabulate(data, headings, tablefmt="html")
    print tabulate(data, headings, tablefmt="grid")
    msg.attach(MIMEText(body, 'html'))
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(fromaddr, os.environ["EMAIL_PASSWORD"])
    text = msg.as_string()
    server.sendmail(fromaddr, toaddr, text)
    server.quit()
    

def main():
    if len(sys.argv) < 1 :
        print "Usage: python monkey.py"
        exit(1)
    print('\nResource Analytics Monkey : STARTED\n');
    
    global headers, redis
    fetch_all_droplet_ids()
    number_active_prod_servers = len(droplet_ids_map)
    if number_active_prod_servers == 1:
        print('\nResource Analytics Monkey : ABORTED - Need more than 1 active prod server running!\n')
        exit(1)
        
    new_size = instance_sizes[instance_sizes.index(steady_state_instance_size) + 2] 
    for i in range(number_active_prod_servers):
        upsize(new_size)

    time.sleep(180)
    collect_metrics(new_size)
    
    
    new_size = instance_sizes[instance_sizes.index(steady_state_instance_size) + 1] 
    for i in range(number_active_prod_servers):
        downsize(new_size)

    time.sleep(180)
    collect_metrics(new_size)
    
    
    new_size = instance_sizes[instance_sizes.index(steady_state_instance_size)] 
    for i in range(number_active_prod_servers):
        downsize(new_size)

    time.sleep(180)
    collect_metrics(steady_state_instance_size)

    email_report()
    
    print('\nResource Analytics Monkey : COMPLETED\n');

if __name__ == '__main__':
    main()
import boto3
import logging
import os
import sys

_LOGGER = logging.Logger('k8sasghelper')

from constants import (
    PROJECT_NAME, ENV_NAME, MASTER_TAG_NAME,ZONE_ID, DOMAIN_NAME, MV_TTL, 
    EXTERNAL_API_DNS_PREFIX, INTERNAL_API_DNS_PREFIX, LOG_LEVEL
)

def get_instances(client, config, instance_type, return_attribute):
    """Return a list of private DNS addresses for servers matching project, 
    environment, id tag name (which identified it as an etcd or master server),
    and instance state (pending/running)
    """

    servers = []

    if instance_type == 'etcd':
        id_tag_name = config.get(ETCD_TAG_NAME, {})
    elif instance_type == 'master':
        id_tag_name = config.get(MASTER_TAG_NAME, {})
    else:
        _LOGGER.error("Invalid type of server sought by get_instances()")
        exit(1)

    _LOGGER.info(
        "Finding instances from Project {} - Environment {} - tagged {}".format(
            config.get(PROJECT_NAME, {}),
            config.get(ENV_NAME, {}),
            id_tag_name
        ))

    resp = client.describe_instances(
        Filters=[
            {
                'Name': 'tag:Environment',
                'Values': [
                    config.get(ENV_NAME, {})
                    ]
            },
            {
                'Name': 'tag:Project',
                'Values':[
                    config.get(PROJECT_NAME, {})
                    ]
            },
            {
                'Name': 'tag:{}'.format(id_tag_name),
                'Values': [
                    'True', 'true'
                    ]
            },
            {
                'Name': 'instance-state-name',
                'Values': [
                    'running', 'pending'
                ]
            }
        ]
    )

    for i in resp['Reservations']:
        servers.append(
            i['Instances'][0][return_attribute]
        )

    _LOGGER.info("Found instances: {}".format(servers))
    
    return servers

def stash_masters_in_ssm(client, config, master_servers):
    """Upsert a StringList at path /ProjectName/EnvName/masters/privateips with
    a list of the Private IP Addresses found active in EC2
    """

    _LOGGER.info(
        "Stashing Master Instance Private IPs in SSM at Path /{}/{}/masters/privateips: {}".format(
            config.get(PROJECT_NAME, {}),
            config.get(ENV_NAME, {}),
            master_servers
        )
    )

    client.put_parameter(
        Name='/{}/{}/masters/privateips'.format(
            config.get(PROJECT_NAME, {}),
            config.get(ENV_NAME, {})
        ),
        Description='List of Masters Private IPs for Project {} and Environment {}'.format(
            config.get(PROJECT_NAME, {}),
            config.get(ENV_NAME, {})
        ),
        Type='StringList',
        Overwrite=True,
        Value=','.join(master_servers)
    )

    # Verify contents of the SSM Parameter we just stashed
    newcontents = client.get_parameter(
        Name= "/{}/{}/masters/privateips".format(
            config.get(PROJECT_NAME, {}),
            config.get(ENV_NAME, {})
        )
    )

    _LOGGER.info("Contents of {}: {}".format(
            newcontents['Parameter']['Name'],
            newcontents['Parameter']['Value']
        )
    )

def upsert_multivalue_record(client, config, ip_address, dns_prefix):
    """Create a Route53 Record as part of a Multivalue Record Set.
    We're using these in lieu of external/internal Load Balancers
    """

    _LOGGER.debug("Upserting Multivalue Record for DNS Prefix {} with value {}".format(
            dns_prefix,
            ip_address
        )
    )

    resp = client.change_resource_record_sets(
        HostedZoneId=config.get(ZONE_ID, {}),
        ChangeBatch={
            'Comment': 'Updating multivalue record for {}'.format(ip_address),
            'Changes': [
                {
                    'Action': 'UPSERT',
                    'ResourceRecordSet': {
                        'Name': "{}.{}".format(
                            dns_prefix, config.get(DOMAIN_NAME, {})
                        ),
                        'Type': 'A',
                        'SetIdentifier': "{}/{}/{}/{}".format(
                            config.get(PROJECT_NAME, {}),
                            config.get(ENV_NAME, {}),
                            dns_prefix,
                            ip_address
                        ),
                        'MultiValueAnswer': True,
                        'TTL': int(config.get(MV_TTL, {})),
                        'ResourceRecords': [
                            {
                                'Value': ip_address
                            }
                        ]
                    }
                }
            ]
        }
    )
    _LOGGER.debug("Return code for upsert of {}: {}".format(
            ip_address,
            resp['ResponseMetadata']['HTTPStatusCode']
        )
    )
    return resp['ResponseMetadata']['HTTPStatusCode']

def find_stale_multivalue_records(client, config, master_servers, dns_prefix):
    """Returns a list of A Records marked matching this project and which bear
    the designated naming prefix which are not found in the list of active
    master instances. These are deemed to be stale and are marked for deletion.
    """
    stalerecords = []

    filteredrecords = client.list_resource_record_sets(
        HostedZoneId=config.get(ZONE_ID, {}),
        StartRecordName='{}.{}'.format(
            dns_prefix,
            config.get(DOMAIN_NAME, {})
        ),
        StartRecordType='A',
        MaxItems='50'
    )

    _LOGGER.debug("Filtered Records: {}".format(filteredrecords))

    # Filter records some more to ensure we only return those which are not
    # current masters and which match the desired dns_prefix
    for record in filteredrecords['ResourceRecordSets']:
        _LOGGER.debug(record)
        if record['ResourceRecords'][0]['Value'] not in master_servers:
            if record['SetIdentifier'].split('/')[2] == dns_prefix:
                _LOGGER.info("Found Stale Record: {}".format(record))
                stalerecords.append(record)

    return stalerecords
        
def remove_multivalue_record(client, config, record):

    _LOGGER.info("Deleting record with SetIdentifier: {}".format(
        record['SetIdentifier']
        )
    )

    resp = client.change_resource_record_sets(
        HostedZoneId=config.get(ZONE_ID, {}),
        ChangeBatch={
            'Comment': 'Removing multivalue record for {}'.format(
                record['ResourceRecords'][0]['Value']
            ),
            'Changes': [
                {
                    'Action': 'DELETE',
                    'ResourceRecordSet': {
                        'Name': record['Name'],
                        'Type': record['Type'],
                        'SetIdentifier': record['SetIdentifier'],
                        'MultiValueAnswer': record['MultiValueAnswer'],
                        'TTL': record['TTL'],
                        'ResourceRecords': record['ResourceRecords']
                    }
                }
            ]
        }
    )
    return resp['ResponseMetadata']['HTTPStatusCode']

def setup_logging():
    """Configure _LOGGER
    """
    _LOGGER.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s : %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    _LOGGER.addHandler(handler)

def lambda_handler(event, context):
    """Entrypoint for AWS Lambda
    """
    setup_logging()

    config = {
        PROJECT_NAME: os.environ.get(PROJECT_NAME),
        ENV_NAME: os.environ.get(ENV_NAME),
        MASTER_TAG_NAME: os.environ.get(MASTER_TAG_NAME),
        ZONE_ID: os.environ.get(ZONE_ID),
        DOMAIN_NAME: os.environ.get(DOMAIN_NAME),
        MV_TTL: os.environ.get(MV_TTL),
        EXTERNAL_API_DNS_PREFIX: os.environ.get(EXTERNAL_API_DNS_PREFIX, {}),
        INTERNAL_API_DNS_PREFIX: os.environ.get(INTERNAL_API_DNS_PREFIX, {}),
        LOG_LEVEL: os.environ.get(LOG_LEVEL, {})
    }

    try:

        # Establish client connections
        route53 = boto3.client('route53')
        ssm = boto3.client('ssm')
        ec2 = boto3.client('ec2')

        # Get list of Masters by Private DNS
        masterserverspriv = get_instances(ec2, config, "master", "PrivateIpAddress")

        # Get list of Masters by Public IP
        masterserverspub = get_instances(ec2, config, "master", "PublicIpAddress")

        # Stash list of Masters Private DNS in SSM
        stash_masters_in_ssm(ssm, config, masterserverspriv)

        # UPSERT internal API multivalue A-Records for all current Masters
        for server in masterserverspriv:
            upsert_multivalue_record(
                route53,
                config,
                server,
                config.get(INTERNAL_API_DNS_PREFIX, {})
            )

        # UPSERT external API multivalue A-Records for all current Masters
        for server in masterserverspub:
            upsert_multivalue_record(
                route53,
                config,
                server,
                config.get(EXTERNAL_API_DNS_PREFIX, {})
            )

        # Get a list of External API Multivalue A-Records that are stale
        stale_public_records = find_stale_multivalue_records(
            route53,
            config,
            masterserverspub,
            config.get(EXTERNAL_API_DNS_PREFIX, {})
        )

        # Get a list of Internal API Multivalue A-Records that are stale
        stale_private_records = find_stale_multivalue_records(
            route53,
            config,
            masterserverspriv,
            config.get(INTERNAL_API_DNS_PREFIX, {})
        )

        # Purge all stale Internal Multivalue A-Records
        _LOGGER.debug("Stale Private Records: {}".format(stale_private_records))
        if stale_private_records:
            for record in stale_private_records:
                remove_multivalue_record(
                    route53,
                    config,
                    record
                )
        else:
            _LOGGER.info("No stale Private Multivalue records found")

        # Purge all stale External Multivalue A-Records
        _LOGGER.debug("Stale Public Records: {}".format(stale_public_records))
        if stale_public_records:
            for record in stale_public_records:
                remove_multivalue_record(
                    route53,
                    config,
                    record
                )
        else:
            _LOGGER.info("No stale Public Multivalue records found")

        _LOGGER.info("Sync Complete")

    except Exception as e:
        _LOGGER.error(e)
        exit(1)

if __name__ == '__main__':
    lambda_handler({}, {})

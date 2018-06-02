# kubernetes-asg-helper

Lambda Function designed to make life easier when autoscaling etcd and kubernetes masters

## Usage:
`sls deploy --stage dev --region us-east-2 --r53zoneid ABCD50Q8F48Z0 --r53domain some.domain.com --project arbitraryname`

## TO DO

* Write better README
* Fix functionality when no matching masters or etcds are found
* Include logic to switch off unused features
  * for use with etcd only
  * for use with masters only
  * no stashing of masters in SSM Parameter Store
* Tutorial example with Terraform?

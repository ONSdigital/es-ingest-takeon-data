#!/usr/bin/env bash

cd repository
echo Packaging serverless bundle...
serverless package --package pkg
find ./pkg
serverless deploy --verbose;
import json
import logging
import os

import boto3
from es_aws_functions import aws_functions, exception_classes, general_functions
from marshmallow import Schema, fields


class InputSchema(Schema):
    """
    Schema to ensure that environment variables are present and in the correct format.
    These variables are expected by the method, and it will fail to run if not provided.
    :return: None
    """
    checkpoint = fields.Str(required=True)
    method_name = fields.Str(required=True)
    results_bucket_name = fields.Str(required=True)
    takeon_bucket_name = fields.Str(required=True)


def lambda_handler(event, context):
    """
    This method will take the simple bricks survey data and expand it to have seperate
     coloumn for each brick type as expceted by the results pipeline. It'll then send it to
     the Results S3 bucket for further processing.
    :param event: Event object
    :param context: Context object
    :return: JSON String - {"success": boolean, "checkpoint"/"error": integer/string}
    """
    current_module = "Results Ingest - Brick Type - Wrangler"
    error_message = ""
    logger = logging.getLogger("Results Ingest - Brick Type")
    logger.setLevel(10)

    # Define run_id outside of try block
    run_id = 0
    try:
        logger.info("Starting " + current_module)
        # Retrieve run_id before input validation
        # Because it is used in exception handling
        run_id = event['RuntimeVariables']['run_id']

        # ENV vars
        schema = InputSchema()
        config, errors = schema.load(os.environ)
        if errors:
            raise ValueError(f"Error validating environment parameters: {errors}")

        # Environment Variables
        checkpoint = config['checkpoint']
        method_name = config['method_name']
        results_bucket_name = config['results_bucket_name']

        # Runtime Variables
        in_file_name = event['RuntimeVariables']['in_file_name']
        location = event['RuntimeVariables']['location']
        out_file_name = event['RuntimeVariables']['out_file_name']
        outgoing_message_group_id = event['RuntimeVariables']["outgoing_message_group_id"]
        sns_topic_arn = event['RuntimeVariables']['sns_topic_arn']
        sqs_queue_url = event['RuntimeVariables']['queue_url']
        ingestion_parameters = event["RuntimeVariables"]["ingestion_parameters"]

        logger.info("Validated environment parameters.")
        lambda_client = boto3.client('lambda', region_name='eu-west-2')
        input_file = aws_functions.read_from_s3(results_bucket_name,
                                                in_file_name,
                                                file_extension="")

        logger.info("Read from S3.")

        payload = {

            "RuntimeVariables": {
                "data": json.loads(input_file),
                "run_id": run_id,
                "brick_questions": ingestion_parameters["brick_questions"],
                "brick_types": ingestion_parameters["brick_types"]
            },
        }

        method_return = lambda_client.invoke(
         FunctionName=method_name, Payload=json.dumps(payload)
        )
        logger.info("Successfully invoked method.")

        json_response = json.loads(method_return.get('Payload').read().decode("utf-8"))
        logger.info("JSON extracted from method response.")

        if not json_response["success"]:
            raise exception_classes.MethodFailure(json_response['error'])

        aws_functions.save_data(results_bucket_name, out_file_name,
                                json_response["data"], sqs_queue_url,
                                outgoing_message_group_id, location)

        logger.info("Data ready for Results pipeline. Written to S3.")

        aws_functions.send_sns_message(checkpoint, sns_topic_arn, "Ingest.")

    except Exception as e:
        error_message = general_functions.handle_exception(e, current_module,
                                                           run_id, context)
    finally:
        if (len(error_message)) > 0:
            logger.error(error_message)
            raise exception_classes.LambdaFailure(error_message)

    logger.info("Successfully completed module: " + current_module)
    return {"success": True, "checkpoint": checkpoint}

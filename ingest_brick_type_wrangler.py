import json
import logging
import os

import boto3
from es_aws_functions import aws_functions, exception_classes, general_functions
from marshmallow import EXCLUDE, Schema, fields


class EnvironmentSchema(Schema):

    class Meta:
        unknown = EXCLUDE

    def handle_error(self, e, data, **kwargs):
        logging.error(f"Error validating environment params: {e}")
        raise ValueError(f"Error validating environment params: {e}")

    method_name = fields.Str(required=True)
    results_bucket_name = fields.Str(required=True)


class IngestionParamsSchema(Schema):

    class Meta:
        unknown = EXCLUDE

    brick_questions = fields.Dict(required=True)
    brick_types = fields.List(fields.Int(required=True))
    brick_type_column = fields.Str(required=True)


class RuntimeSchema(Schema):

    class Meta:
        unknown = EXCLUDE

    def handle_error(self, e, data, **kwargs):
        logging.error(f"Error validating runtime params: {e}")
        raise ValueError(f"Error validating runtime params: {e}")

    bpm_queue_url = fields.Str(required=True)
    environment = fields.Str(required=True)
    in_file_name = fields.Str(required=True)
    ingestion_parameters = fields.Nested(IngestionParamsSchema, required=True)
    out_file_name = fields.Str(required=True)
    sns_topic_arn = fields.Str(required=True)
    survey = fields.Str(required=True)
    total_steps = fields.Int(required=True)


def lambda_handler(event, context):
    """
    This method will take the simple bricks survey data and expand it to have seperate
     column for each brick type as expceted by the results pipeline. It'll then send it
     to the Results S3 bucket for further processing.
    :param event: Event object
    :param context: Context object
    :return: JSON String - {"success": boolean, "error": string}
    """
    current_module = "Results Ingest - Brick Type - Wrangler"
    error_message = ""
    # Variables required for error handling.
    bpm_queue_url = None
    run_id = 0
    try:
        # Retrieve run_id before input validation
        # Because it is used in exception handling
        run_id = event["RuntimeVariables"]["run_id"]

        # Load variables.
        environment_variables = EnvironmentSchema().load(os.environ)
        runtime_variables = RuntimeSchema().load(event["RuntimeVariables"])

        # Environment Variables.
        method_name = environment_variables["method_name"]
        results_bucket_name = environment_variables["results_bucket_name"]

        # Runtime Variables.
        bpm_queue_url = runtime_variables["bpm_queue_url"]
        environment = runtime_variables["environment"]
        in_file_name = runtime_variables["in_file_name"]
        ingestion_parameters = runtime_variables["ingestion_parameters"]
        out_file_name = runtime_variables["out_file_name"]
        sns_topic_arn = runtime_variables["sns_topic_arn"]
        survey = runtime_variables["survey"]
        total_steps = runtime_variables["total_steps"]
    except Exception as e:
        error_message = general_functions.handle_exception(e, current_module, run_id,
                                                           context=context,
                                                           bpm_queue_url=bpm_queue_url)
        raise exception_classes.LambdaFailure(error_message)

    try:
        logger = general_functions.get_logger(survey, current_module, environment,
                                              run_id)
    except Exception as e:
        error_message = general_functions.handle_exception(e, current_module, run_id,
                                                           context=context,
                                                           bpm_queue_url=bpm_queue_url)
        raise exception_classes.LambdaFailure(error_message)

    try:
        logger.info("Started - retrieved configuration variables.")
        # Send in progress status to BPM.
        status = "IN PROGRESS"
        current_step_num = 1
        aws_functions.send_bpm_status(bpm_queue_url, current_module, status, run_id,
                                      current_step_num, total_steps)
        # Set up client.
        lambda_client = boto3.client("lambda", region_name="eu-west-2")
        data_df = aws_functions.read_dataframe_from_s3(results_bucket_name, in_file_name)

        logger.info("Retrieved data from S3.")
        data_json = data_df.to_json(orient="records")

        payload = {

            "RuntimeVariables": {
                "bpm_queue_url": bpm_queue_url,
                "brick_questions": ingestion_parameters["brick_questions"],
                "brick_types": ingestion_parameters["brick_types"],
                "brick_type_column": ingestion_parameters["brick_type_column"],
                "data": json.loads(data_json),
                "environment": environment,
                "run_id": run_id,
                "survey": survey
            },
        }

        method_return = lambda_client.invoke(
            FunctionName=method_name, Payload=json.dumps(payload)
        )
        logger.info("Successfully invoked method.")

        json_response = json.loads(method_return.get("Payload").read().decode("utf-8"))
        logger.info("JSON extracted from method response.")

        if not json_response["success"]:
            raise exception_classes.MethodFailure(json_response["error"])

        aws_functions.save_to_s3(results_bucket_name, out_file_name,
                                 json_response["data"])

        logger.info("Data ready for Results pipeline. Written to S3.")

        aws_functions.send_sns_message(sns_topic_arn, "Ingest.")
    except Exception as e:
        error_message = general_functions.handle_exception(e, current_module, run_id,
                                                           context=context,
                                                           bpm_queue_url=bpm_queue_url)
    finally:
        if (len(error_message)) > 0:
            logger.error(error_message)
            raise exception_classes.LambdaFailure(error_message)

    logger.info("Successfully completed module.")

    # Send end status to BPM.
    status = "DONE"
    aws_functions.send_bpm_status(bpm_queue_url, current_module, status, run_id,
                                  current_step_num, total_steps)

    return {"success": True}

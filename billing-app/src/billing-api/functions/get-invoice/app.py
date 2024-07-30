import json
import logging
import os
from datetime import datetime
from fastapi import HTTPException, status
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from bson import ObjectId
import boto3
from fastapi.responses import JSONResponse
import requests


class InvoiceItem(BaseModel):
    description: str
    quantity: int
    price: float


class Invoice(BaseModel):
    client: str
    date: datetime
    total: float
    items: list


def get_secret(resource: str):
    """
    Retrieve credentials from AWS Secrets Manager.

    Args:
        resource (str): Indicates the resource from which the secrets will be obtained.
    Returns:
        dict: Credentials retrieved from the secret.
    Raises:
        HTTPException: If an error occurs while retrieving secrets.
    """
    resources = {
        "mongodb": "MONGODB_SECRET_NAME",
        "alegra": "ALEGRA_API_KEY"
    }
    secret_name = os.environ[resources.get(resource)]
    region_name = os.environ["AWS_REGION_NAME"]

    try:
        session = boto3.session.Session()
        client = session.client(service_name="secretsmanager", region_name=region_name)
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        return json.loads(get_secret_value_response["SecretString"])
    except Exception as err:
        logging.error(f"Unexpected error: {str(err)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(err)
        )


def database_connection(secrets: dict):
    """
    Establish a connection to the MongoDB database.

    Args:
        secrets (dict): MongoDB credentials.
    Returns:
        MongoClient: A MongoDB client.
    Raises:
        HTTPException: If an error occurs while connecting to the database.
    """
    try:
        username = secrets["username"]
        password = secrets["password"]
        host = os.environ["MONGODB_HOST"]

        connection_string = (
            f"mongodb+srv://{username}:{password}@{host}/"
            f"?retryWrites=true&w=majority"
        )
        client = MongoClient(connection_string, server_api=ServerApi("1"))
        return client
    except Exception as err:
        logging.error(f"Unexpected error: {str(err)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(err)
        )


def get_invoices_from_alegra():
    """
    Retrieve a list of invoices from Alegra Billing.

    Returns:
        list: List of invoices retrieved from Alegra.
    """
    API_URL = "https://api.alegra.com/api/v1/"
    API_KEY = os.getenv("ALEGRA_API_KEY")
    url = f"{API_URL}/invoices"
    headers = {
        "Authorization": f"Basic {API_KEY}",
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers=headers)
    return response.json()


def get_invoice_details_from_alegra(invoice_id: str):
    """
    Retrieve the details of a specific invoice from Alegra Billing.

    Args:
        invoice_id (str): The ID of the invoice to retrieve.
    Returns:
        dict: Details of the invoice retrieved from Alegra.
    """
    API_URL = "https://api.alegra.com/api/v1/"
    API_KEY = os.getenv("ALEGRA_API_KEY")
    url = f"{API_URL}/invoices/{invoice_id}"
    headers = {
        "Authorization": f"Basic {API_KEY}",
        "Content-Type": "application/json"
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found in Alegra")
    return response.json()


def data_response(status_code: int, message: str, extra_data: dict = None):
    """
    Creates a data response in JSON format.

    Args:
        status_code (int): The HTTP status code to return.
        message (str): The message to include in the response body.
        extra_data (dict, optional): Additional data to include in the response body. Defaults to None.

    Returns:
        dict: The JSON response to be returned by the Lambda function.
    """
    body = {"message": message}
    if extra_data is not None:
        body.update(extra_data)
    return {
        "statusCode": status_code,
        "body": json.dumps(body, default=str),
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Max-Age": "3600"
        }
    }


def get_invoices(event, context):
    """
    Retrieve a list of invoices from MongoDB and Alegra Billing.

    Args:
        event (dict): The AWS Lambda event object containing request parameters.
        context (obj): The AWS Lambda context object (not used in this function).

    Returns:
        dict: Response containing a list of invoices.
    """
    try:
        # Retrieve MongoDB invoices
        secrets = get_secret("mongodb")
        client = database_connection(secrets)
        db = client[os.environ["MONGO_DB_NAME"]]
        invoices_collection = db.get_collection("invoices")
        invoices = list(invoices_collection.find())

        # Retrieve Alegra invoices
        alegra_invoices = get_invoices_from_alegra()

        combined_invoices = {
            "mongodb_invoices": invoices,
            "alegra_invoices": alegra_invoices
        }
        return data_response(status_code=status.HTTP_200_OK, message="Invoices retrieved successfully",
                             extra_data=combined_invoices)
    except HTTPException as err:
        return data_response(status_code=err.status_code, message=err.detail)
    except Exception as err:
        logging.error(f"Unexpected error: {str(err)}", exc_info=True)
        return data_response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=str(err))


def get_invoice(event, context):
    """
    Retrieve the details of a specific invoice from MongoDB and Alegra Billing.

    Args:
        event (dict): The AWS Lambda event object containing request parameters.
        context (obj): The AWS Lambda context object (not used in this function).

    Returns:
        dict: Response containing the details of the invoice.
    """
    try:
        invoice_id = event["pathParameters"]["invoice_id"]

        # Retrieve MongoDB invoice
        secrets = get_secret("mongodb")
        client = database_connection(secrets)
        db = client[os.environ["MONGO_DB_NAME"]]
        invoices_collection = db.get_collection("invoices")
        invoice = invoices_collection.find_one({"_id": ObjectId(invoice_id)})

        if not invoice:
            # Retrieve Alegra invoice if not found in MongoDB
            invoice = get_invoice_details_from_alegra(invoice_id)

        return data_response(status_code=status.HTTP_200_OK, message="Invoice retrieved successfully",
                             extra_data=invoice)
    except HTTPException as err:
        return data_response(status_code=err.status_code, message=err.detail)
    except Exception as err:
        logging.error(f"Unexpected error: {str(err)}", exc_info=True)
        return data_response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=str(err))

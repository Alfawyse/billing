import json
import logging
import os
from datetime import datetime
from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError
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


def update_invoice_in_alegra(invoice_id: str, invoice: dict):
    """
    Update an existing invoice in Alegra Billing.

    Args:
        invoice_id (str): The ID of the invoice to be updated.
        invoice (dict): Updated invoice data.
    Returns:
        dict: The updated invoice details from Alegra.
    """
    API_URL = "https://api.alegra.com/api/v1/"
    API_KEY = os.getenv("ALEGRA_API_KEY")
    url = f"{API_URL}/invoices/{invoice_id}"
    headers = {
        "Authorization": f"Basic {API_KEY}",
        "Content-Type": "application/json"
    }
    response = requests.put(url, headers=headers, json=invoice)
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


def update_invoice(event, context):
    """
    Update an existing invoice in MongoDB and Alegra Billing.

    Args:
        event (dict): The AWS Lambda event object containing request parameters.
        context (obj): The AWS Lambda context object (not used in this function).

    Returns:
        dict: Response containing the updated invoice details.
    """
    try:
        invoice_id = event["pathParameters"]["invoice_id"]
        body = json.loads(event["body"])
        invoice = Invoice(**body)

        # Retrieve MongoDB connection
        secrets = get_secret("mongodb")
        client = database_connection(secrets)
        db = client[os.environ["MONGO_DB_NAME"]]
        invoices_collection = db.get_collection("invoices")

        # Update invoice in MongoDB
        invoice_data = invoice.dict()
        invoice_data["updated_at"] = datetime.utcnow()
        update_result = invoices_collection.update_one(
            {"_id": ObjectId(invoice_id)},
            {"$set": invoice_data}
        )

        if update_result.matched_count == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found in MongoDB")

        # Update invoice in Alegra
        alegra_invoice_data = {
            "date": invoice.date.strftime("%Y-%m-%d"),
            "client": invoice.client,
            "items": [
                {
                    "description": item.description,
                    "quantity": item.quantity,
                    "price": item.price
                } for item in invoice.items
            ],
            "total": invoice.total
        }
        alegra_invoice = update_invoice_in_alegra(invoice_id, alegra_invoice_data)

        return data_response(status_code=status.HTTP_200_OK, message="Invoice updated successfully",
                             extra_data=alegra_invoice)
    except ValidationError as err:
        logging.error(err)
        err_msg = ", ".join(f"{error['loc']}: {error['msg']}" for error in err.errors())
        return data_response(status_code=status.HTTP_400_BAD_REQUEST, message=err_msg)
    except HTTPException as err:
        return data_response(status_code=err.status_code, message=err.detail)
    except Exception as err:
        logging.error(f"Unexpected error: {str(err)}", exc_info=True)
        return data_response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=str(err))

import json
import logging
import os
import base64
from datetime import datetime
from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from bson import ObjectId
import boto3
import requests


class InvoiceItem(BaseModel):
    id: int
    name: str
    discount: int
    price: float
    quantity: int


class Payment(BaseModel):
    date: datetime
    amount: float
    paymentMethod: str


class Invoice(BaseModel):
    client: dict
    paymentForm: str
    items: list[InvoiceItem]
    payments: list[Payment]
    dueDate: datetime
    date: datetime


class Contact(BaseModel):
    nameObject: dict
    identificationObject: dict
    kindOfPerson: str
    regime: str
    name: str
    mobile: str
    email: str


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
        "mongodb": "MONGODB_SECRET_NAME"
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


def get_alegra_auth_header(email: str, token: str):
    """
    Generate the Basic Auth header for Alegra API.

    Args:
        email (str): User email for Alegra.
        token (str): API token for Alegra.

    Returns:
        str: Basic Auth header value.
    """
    auth_str = f"{email}:{token}"
    auth_bytes = auth_str.encode('utf-8')
    auth_base64 = base64.b64encode(auth_bytes).decode('utf-8')
    return f"Basic {auth_base64}"


def create_contact_in_alegra(contact_data: dict, email: str, token: str):
    """
    Create a new contact in Alegra.

    Args:
        contact_data (dict): Contact data to be created in Alegra.
        email (str): User email for Alegra.
        token (str): API token for Alegra.

    Returns:
        dict: The created contact details from Alegra.
    """
    API_URL = "https://api.alegra.com/api/v1/contacts"
    headers = {
        "Authorization": get_alegra_auth_header(email, token),
        "Content-Type": "application/json"
    }
    response = requests.post(API_URL, headers=headers, json=contact_data)
    response.raise_for_status()  # Raise an exception for HTTP errors
    return response.json()


def create_invoice_in_alegra(invoice: dict, email: str, token: str):
    """
    Create a new invoice in Alegra.

    Args:
        invoice (dict): Invoice data to be created in Alegra.
        email (str): User email for Alegra.
        token (str): API token for Alegra.

    Returns:
        dict: The created invoice details from Alegra.
    """
    API_URL = "https://api.alegra.com/api/v1/invoices"
    headers = {
        "Authorization": get_alegra_auth_header(email, token),
        "Content-Type": "application/json"
    }
    response = requests.post(API_URL, headers=headers, json=invoice)
    response.raise_for_status()  # Raise an exception for HTTP errors
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


def create_invoice(event, context):
    """
    Create a new invoice in MongoDB and Alegra Billing.

    Args:
        event (dict): The AWS Lambda event object containing request parameters.
        context (obj): The AWS Lambda context object (not used in this function).

    Returns:
        dict: Response containing the created invoice details.
    """
    try:
        body = json.loads(event["body"])

        # Extract contact data
        contact_data = body.get("contact")
        if not contact_data:
            raise ValidationError("Contact data is missing")

        contact = Contact(**contact_data)

        # Extract invoice data
        invoice_data = body.get("invoice")
        if not invoice_data:
            raise ValidationError("Invoice data is missing")

        invoice = Invoice(**invoice_data)

        # Alegra credentials
        alegra_email = os.getenv("ALEGRA_EMAIL")
        alegra_token = os.getenv("ALEGRA_API_TOKEN")

        # Create contact in Alegra
        alegra_contact = create_contact_in_alegra(contact.dict(), alegra_email, alegra_token)
        alegra_contact_id = alegra_contact["id"]

        # Retrieve MongoDB connection
        secrets = get_secret("mongodb")
        client = database_connection(secrets)
        db = client[os.environ["MONGO_DB_NAME"]]
        contacts_collection = db.get_collection("contacts")

        # Save contact ID in MongoDB
        contact_data["_id"] = alegra_contact_id
        contact_data["created_at"] = datetime.utcnow()
        contact_data["updated_at"] = datetime.utcnow()
        contacts_collection.insert_one(contact_data)

        # Update invoice data with Alegra contact ID
        invoice.client = {"id": alegra_contact_id}

        # Create invoice in MongoDB
        invoices_collection = db.get_collection("invoices")
        invoice_data = invoice.dict()
        invoice_data["_id"] = str(ObjectId())
        invoice_data["created_at"] = datetime.utcnow()
        invoice_data["updated_at"] = datetime.utcnow()
        invoices_collection.insert_one(invoice_data)

        # Create invoice in Alegra
        alegra_invoice = create_invoice_in_alegra(invoice.dict(), alegra_email, alegra_token)

        return data_response(status_code=status.HTTP_201_CREATED, message="Invoice created successfully",
                             extra_data=alegra_invoice)
    except ValidationError as err:
        logging.error(err)
        err_msg = ", ".join(f"{error['loc']}: {error['msg']}" for error in err.errors())
        return data_response(status_code=status.HTTP_400_BAD_REQUEST, message=err_msg)
    except HTTPException as err:
        return data_response(status_code=err.status_code, message=err.detail)
    except requests.exceptions.RequestException as err:
        logging.error(f"HTTP error: {str(err)}", exc_info=True)
        return data_response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=str(err))
    except Exception as err:
        logging.error(f"Unexpected error: {str(err)}", exc_info=True)
        return data_response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=str(err))




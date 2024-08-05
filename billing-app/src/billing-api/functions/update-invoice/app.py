import json
import logging
import os
from datetime import datetime
from pydantic import BaseModel, ValidationError
import base64
import requests
import boto3


class InvoiceItem(BaseModel):
    description: str
    quantity: int
    price: float


class Invoice(BaseModel):
    client: str
    date: datetime
    total: float
    items: list

def get_alegra_auth_header(email, token):
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


def get_invoice_details_from_alegra(invoice_id: str):
    """
    Retrieve the details of a specific invoice from Alegra Billing.

    Args:
        invoice_id (str): The ID of the invoice to retrieve.
    Returns:
        dict: Details of the invoice retrieved from Alegra.
    """
    API_URL = f"https://api.alegra.com/api/v1/invoices/{invoice_id}"
    email = os.getenv("ALEGRA_EMAIL")
    token = os.getenv("ALEGRA_API_KEY")

    # Get the authorization header
    auth_header = get_alegra_auth_header(email, token)

    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json"
    }

    logging.info(f"Request URL: {API_URL}")
    logging.info(f"Request Headers: {headers}")

    response = requests.get(API_URL, headers=headers)

    logging.info(f"Response Status Code: {response.status_code}")
    logging.info(f"Response Content: {response.content}")

    if response.status_code == 404:
        raise Exception("Invoice not found in Alegra")
    elif response.status_code != 200:
        raise Exception(f"Failed to retrieve invoice: {response.text}")

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


def lambda_handler(event, context):
    """
    Retrieve the details of a specific invoice from Alegra Billing.

    Args:
        event (dict): The AWS Lambda event object containing request parameters.
        context (obj): The AWS Lambda context object (not used in this function).

    Returns:
        dict: Response containing the details of the invoice.
    """
    try:
        invoice_id = event["pathParameters"]["invoice_id"]

        # Retrieve Alegra invoice
        invoice = get_invoice_details_from_alegra(invoice_id)

        return data_response(status_code=200, message="Invoice retrieved successfully", extra_data=invoice)
    except ValidationError as err:
        logging.error(err)
        err_msg = ", ".join(f"{error['loc']}: {error['msg']}" for error in err.errors())
        return data_response(status_code=400, message=err_msg)
    except Exception as err:
        logging.error(f"Unexpected error: {str(err)}", exc_info=True)
        return data_response(status_code=500, message=str(err))

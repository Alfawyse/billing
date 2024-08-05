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
import base64


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

def get_invoices_from_alegra(invoice_id: str):
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found in Alegra")
    elif response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

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

def get_invoice(event, context):
        """
        Retrieve the details of a specific invoice from Alegra Billing.

        Args:
            invoice_id (str): The ID of the invoice to retrieve.

        Returns:
            dict: Response containing the details of the invoice.
        """
        try:
            # Retrieve Alegra invoice
            invoice = get_invoices_from_alegra(invoice_id)

            return data_response(status_code=status.HTTP_200_OK, message="Invoice retrieved successfully",
                                 extra_data=invoice)
        except HTTPException as err:
            return data_response(status_code=err.status_code, message=err.detail)
        except Exception as err:
            logging.error(f"Unexpected error: {str(err)}", exc_info=True)
            return data_response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=str(err))

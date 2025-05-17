from datetime import datetime  # Added for spending status calculations
from datetime import timezone

from dotenv import load_dotenv

load_dotenv()

import json  # Ensure json is imported at the top
import os  # Added for API key

import httpx  # Added for making HTTP requests to Rye
from fastapi import FastAPI
from fastmcp import FastMCP, Image
from firecrawl import FirecrawlApp  # Added for Firecrawl
from supabase import Client, create_client  # Added for supabase-py

# 3. Create the FastMCP server instance
mcp_server = FastMCP("Poltergeist MCP Server 👻")


# 4. Define MCP tools on the mcp_server instance
@mcp_server.tool()
def get_server_status() -> str:
    """Returns the current status of the Poltergeist MCP server."""
    return "Poltergeist MCP Server is running and ready to haunt... I mean, help!"


@mcp_server.tool()
async def research_products(query: str) -> dict:
    """Researches products based on a query using Firecrawl and returns a list of results."""
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        return {"error": "FIRECRAWL_API_KEY not set."}

    try:
        firecrawl_client = FirecrawlApp(api_key=api_key)
        # Firecrawl search API: use page_options keyword, not "params"
        search_resp = firecrawl_client.search(query, limit=10)
        search_results = (
            search_resp.data if hasattr(search_resp, "data") else search_resp
        )

        # Let's extract and return relevant info, like URLs and snippets/titles
        processed_results = []
        if search_results:
            for result in search_results:
                processed_results.append(
                    {
                        "title": result.get("title", "N/A"),
                        "url": result.get("url", "N/A"),
                        "snippet": result.get("description", ""),
                    }
                )
        else:
            # Handle cases where the output might be different, e.g. a single dict or error
            # Based on firecrawl client, .search returns List[SearchResult]
            # If it's not a list, it might be an error response from the lib itself or an unexpected format
            return {
                "error": "Unexpected format from Firecrawl search.",
                "details": str(search_results),
            }

        return {"results": processed_results}
    except Exception as e:
        return {"error": f"An error occurred during Firecrawl search: {str(e)}"}


@mcp_server.tool()
async def request_amazon_product_tracking(product_url: str) -> dict:
    """Requests Rye to start tracking an Amazon product by its URL and returns the Rye productId."""
    rye_auth_header = os.environ.get("RYE_AUTH_HEADER")
    rye_shopper_ip = os.environ.get("RYE_SHOPPER_IP")
    rye_graphql_endpoint = (
        "https://staging.graphql.api.rye.com/v1/query"  # Using staging
    )

    if not all([rye_auth_header, rye_shopper_ip]):
        return {
            "error": "RYE_AUTH_HEADER or RYE_SHOPPER_IP not set in environment variables."
        }

    headers = {
        "Authorization": rye_auth_header,
        "Rye-Shopper-IP": rye_shopper_ip,
        "Content-Type": "application/json",
    }

    query = """
    mutation RequestAmazonProductByURL($input: RequestAmazonProductByURLInput!) {
        requestAmazonProductByURL(input: $input) {
            productId
        }
    }
    """
    variables = {"input": {"url": product_url}}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                rye_graphql_endpoint,
                json={"query": query, "variables": variables},
                headers=headers,
            )
            response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

            response_data = response.json()
            if response_data.get("errors"):
                return {
                    "error": "GraphQL error from Rye",
                    "details": response_data["errors"],
                }

            product_id = (
                response_data.get("data", {})
                .get("requestAmazonProductByURL", {})
                .get("productId")
            )
            if not product_id:
                return {
                    "error": "productId not found in Rye response",
                    "details": response_data,
                }

            return {"productId": product_id}

    except httpx.HTTPStatusError as e:
        return {
            "error": f"HTTP error occurred while contacting Rye: {e.response.status_code}",
            "details": e.response.text,
        }
    except httpx.RequestError as e:
        return {"error": f"Request error occurred while contacting Rye: {str(e)}"}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}


@mcp_server.tool()
async def fetch_amazon_product_details(product_id: str) -> dict:
    """Fetch detailed info for an Amazon product already tracked in Rye using its productId / ASIN."""
    rye_auth_header = os.environ.get("RYE_AUTH_HEADER")
    rye_shopper_ip = os.environ.get("RYE_SHOPPER_IP")
    rye_graphql_endpoint = "https://staging.graphql.api.rye.com/v1/query"

    if not all([rye_auth_header, rye_shopper_ip]):
        return {
            "error": "RYE_AUTH_HEADER or RYE_SHOPPER_IP not set in environment variables."
        }

    headers = {
        "Authorization": rye_auth_header,
        "Rye-Shopper-IP": rye_shopper_ip,
        "Content-Type": "application/json",
    }

    query = """
    query ProductDetails($input: ProductByIDInput!) {
        product: productByID(input: $input) {
            title
            url
            isAvailable
            price { displayValue value currency }
            images { url }
            ... on AmazonProduct {
                ASIN
            }
        }
    }
    """
    variables = {"input": {"id": product_id, "marketplace": "AMAZON"}}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                rye_graphql_endpoint,
                json={"query": query, "variables": variables},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errors"):
                return {"error": "GraphQL error", "details": data["errors"]}
            product = data.get("data", {}).get("product")
            if not product:
                return {"error": "Product not found", "details": data}
            # convert images to fastmcp.Image objects for Claude previews
            img_objs = []
            for img in product.get("images", []):
                if isinstance(img, dict) and img.get("url"):
                    img_objs.append(Image(img["url"]))
            product["image_previews"] = img_objs
            return product
    except httpx.HTTPStatusError as e:
        return {
            "error": f"HTTP error {e.response.status_code}",
            "details": e.response.text,
        }
    except Exception as e:
        return {"error": str(e)}


# 5. Get the ASGI app from FastMCP using SSE transport (easier for proxy clients)
mcp_asgi_app = mcp_server.http_app(transport="sse")  # Provides /sse endpoint

# Create FastAPI app with lifespan
app = FastAPI(lifespan=mcp_asgi_app.router.lifespan_context)


@app.get("/")
async def root():
    return {"message": "Poltergeist Server is alive! FastAPI root accessible."}


# Mount at /poltergeist_mcp
app.mount("/poltergeist_mcp", mcp_asgi_app)

# -------------------- Supabase Tools --------------------

# -------------------- Cart / Checkout tools --------------------


@mcp_server.tool()
async def create_amazon_cart(product_id: str, quantity: int = 1) -> dict:
    """Create a Rye cart containing the given Amazon product. Returns cart info including cartId, cost, and items."""
    rye_auth_header = os.environ.get("RYE_AUTH_HEADER")
    rye_shopper_ip = os.environ.get("RYE_SHOPPER_IP")
    rye_graphql_endpoint = "https://staging.graphql.api.rye.com/v1/query"

    if not all([rye_auth_header, rye_shopper_ip]):
        return {"error": "RYE_AUTH_HEADER or RYE_SHOPPER_IP not set"}

    headers = {
        "Authorization": rye_auth_header,
        "Rye-Shopper-IP": rye_shopper_ip,
        "Content-Type": "application/json",
    }

    # Updated mutation to fetch more details, especially stores and cartLines
    mutation = """
    mutation CreateCart($input: CartCreateInput!) {
        createCart(input: $input) {
            cart {
                id
                cost {
                    total { value displayValue currency }
                    subtotal { value displayValue currency }
                    shipping { value displayValue currency }
                    isEstimated
                }
                stores {
                    ... on AmazonStore {
                        cartLines {
                            quantity
                            product {
                                id
                                title
                            }
                        }
                        errors { # Errors specific to this store
                            code
                            message
                        }
                    }
                    # Could add ShopifyStore or other types if needed
                }
            }
            errors { # Top-level errors for the createCart mutation
                code
                message
            }
        }
    }
    """
    input_obj = {
        # "cartSettings": {"amazonSettings": {"fulfilledByAmazon": True}}, # Let's simplify and remove this for now
        "items": {
            "amazonCartItemsInput": [{"quantity": quantity, "productId": product_id}]
        },
    }

    variables = {"input": input_obj}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                rye_graphql_endpoint,
                json={"query": mutation, "variables": variables},
                headers=headers,
            )
            resp.raise_for_status()  # Handles HTTP-level errors

            response_data = resp.json()

            if response_data.get("errors"):
                return {
                    "error": "GraphQL top-level error during cart creation",
                    "details": response_data["errors"],
                }

            create_cart_payload = response_data.get("data", {}).get("createCart")
            if not create_cart_payload:
                return {
                    "error": "Cart creation failed, 'createCart' payload missing",
                    "details": response_data,
                }

            if create_cart_payload.get("errors"):
                return {
                    "error": "GraphQL error within createCart mutation result",
                    "details": create_cart_payload["errors"],
                }

            cart_object = create_cart_payload.get("cart")
            if not cart_object or not cart_object.get("id"):
                return {
                    "error": "Cart ID not found in successful cart creation",
                    "details": create_cart_payload,
                }

            # Stricter validation: Check if stores and cartLines exist and are populated
            if (
                not cart_object.get("stores")
                or not isinstance(cart_object["stores"], list)
                or len(cart_object["stores"]) == 0
                or not cart_object["stores"][0].get("cartLines")
                or not isinstance(cart_object["stores"][0]["cartLines"], list)
                or len(cart_object["stores"][0]["cartLines"]) == 0
            ):
                return {
                    "error": "Cart created but appears empty or item not added successfully.",
                    "details": "No stores or cartLines found in the response, or they are empty.",
                    "cart_details_received": cart_object,  # include what we got for debugging
                }

            # Check for store-level errors
            for store in cart_object.get("stores", []):
                if store.get("errors") and len(store["errors"]) > 0:
                    return {
                        "error": "Store-level error reported during cart creation.",
                        "details": store["errors"],
                        "cart_details_received": cart_object,
                    }

            return create_cart_payload  # Return the successful payload

    except httpx.HTTPStatusError as e:
        return {
            "error": f"HTTP error during cart creation: {e.response.status_code}",
            "details": e.response.text,
        }
    except Exception as e:
        return {"error": f"An unexpected error occurred during cart creation: {str(e)}"}


# Tool to get cart details
@mcp_server.tool()
async def get_rye_cart_details(cart_id: str) -> dict:
    """Fetches the full details of a given Rye cart by its ID."""
    rye_auth_header = os.environ.get("RYE_AUTH_HEADER")
    rye_shopper_ip = os.environ.get("RYE_SHOPPER_IP")
    rye_graphql_endpoint = "https://staging.graphql.api.rye.com/v1/query"

    if not all([rye_auth_header, rye_shopper_ip]):
        return {"error": "RYE_AUTH_HEADER or RYE_SHOPPER_IP not set"}

    headers = {
        "Authorization": rye_auth_header,
        "Rye-Shopper-IP": rye_shopper_ip,
        "Content-Type": "application/json",
    }

    # Adjusted query based on error hints: getCart returns an object
    # that has a 'cart' field (of type Cart) and an 'errors' field.
    query = """
    query GetCart($id: ID!) {
      getCart(id: $id) {
        cart {
          id
          cost {
            total { value currency }
            subtotal { value currency }
            shipping { value currency }
            tax { value currency }
          }
          stores {
            ... on AmazonStore {
              cartLines {
                quantity
                product {
                  id
                  title
                  price { value currency }
                }
              }
            }
          }
        }
        errors { code message }
      }
    }
    """
    variables = {"id": cart_id}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                rye_graphql_endpoint,
                json={"query": query, "variables": variables},
                headers=headers,
            )
            resp.raise_for_status()
            response_data = resp.json()

            # Check for GraphQL errors at the query execution level (e.g. malformed query, though less likely here)
            if response_data.get("errors"):
                return {
                    "error": "GraphQL top-level execution error fetching cart details",
                    "details": response_data["errors"],
                }

            get_cart_response_payload = response_data.get("data", {}).get("getCart")
            if not get_cart_response_payload:
                return {
                    "error": "No 'getCart' payload in response data",
                    "details": response_data,
                }

            # Check for errors returned by the getCart operation itself
            if get_cart_response_payload.get("errors"):
                return {
                    "error": "GraphQL error reported by getCart operation",
                    "details": get_cart_response_payload["errors"],
                }

            cart_details = get_cart_response_payload.get("cart")
            if not cart_details:
                return {
                    "error": "Could not retrieve nested 'cart' details or cart not found",
                    "details": get_cart_response_payload,
                }

            return cart_details  # Return the actual cart object

    except httpx.HTTPStatusError as e:
        return {
            "error": f"HTTP error fetching cart: {e.response.status_code}",
            "details": e.response.text,
        }
    except Exception as e:
        return {"error": f"Unexpected error fetching cart: {str(e)}"}


@mcp_server.tool()
async def checkout_amazon_cart(cart_id: str, buyer_info: dict) -> dict:
    """Stores checkout event in Supabase without contacting Rye. buyer_info must include at least an email."""
    # Standalone checkout: record the cart event in Supabase without actual transaction
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        return {"error": "Supabase URL or service role key not configured."}
    # Fetch cart details to capture cost and items
    cart_details = await get_rye_cart_details(cart_id)
    print(
        f"checkout_amazon_cart: cart_details = {cart_details}"
    )  # Debug raw cart_details
    if not isinstance(cart_details, dict):
        return {"error": "Invalid cart details received", "details": str(cart_details)}
    if cart_details.get("error"):
        return {
            "error": "Failed to fetch cart details before checkout",
            "details": cart_details,
        }
    # Safely extract cost fields
    cost_info = cart_details.get("cost") or {}
    # Fallback: compute total as subtotal + shipping + tax (values come back in cents)
    subtotal_raw = float((cost_info.get("subtotal") or {}).get("value") or 0)
    shipping_raw = float((cost_info.get("shipping") or {}).get("value") or 0)
    tax_raw = float((cost_info.get("tax") or {}).get("value") or 0)
    # convert cents to dollars
    total_value = (subtotal_raw + shipping_raw + tax_raw) / 100
    # assume single currency across fields
    total_currency = (
        (cost_info.get("subtotal") or {}).get("currency")
        or (cost_info.get("shipping") or {}).get("currency")
        or (cost_info.get("tax") or {}).get("currency")
    )
    # Safely build items snapshot
    items_snapshot = []
    for store in cart_details.get("stores") or []:
        for line in store.get("cartLines") or []:
            prod = line.get("product") or {}
            price = prod.get("price") or {}
            items_snapshot.append(
                {
                    "productId": prod.get("id"),
                    "title": prod.get("title"),
                    "quantity": line.get("quantity"),
                    "price_value": price.get("value"),
                    "price_currency": price.get("currency"),
                }
            )
    try:
        supabase_client = create_client(supabase_url, supabase_key)
        # Build full order record
        order_data = {
            "rye_order_id": None,
            "rye_cart_id": cart_id,
            "user_identifier": buyer_info.get("email"),
            "status": "CREATED",
            "total_amount_value": total_value,
            "total_amount_currency": total_currency,
            "items_snapshot": items_snapshot,
        }
        print(f"checkout_amazon_cart: inserting order_data = {order_data}")  # Debug
        resp = supabase_client.table("orders").insert(order_data).execute()
        if hasattr(resp, "error") and resp.error:
            return {
                "error": "Supabase insert failed",
                "details": resp.error.message or str(resp.error),
                "order_data": order_data,
            }
        return {
            "status": "success",
            "order_data": order_data,
            "supabase_insert": resp.data,
        }
    except Exception as e:
        return {"error": f"Checkout failed: {str(e)}"}


# -------------------- Order Retrieval Tool --------------------
@mcp_server.tool()
async def list_my_purchases(limit: int = 10) -> dict:
    """Fetches the latest `limit` purchases from the Supabase orders table."""
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        return {"error": "Supabase URL or key not set in environment."}
    try:
        supabase_client: Client = create_client(supabase_url, supabase_key)
        response = (
            supabase_client.table("orders")
            .select("*")
            .order("ordered_at", desc=True)
            .limit(limit)
            .execute()
        )
        if hasattr(response, "error") and response.error:
            return {
                "error": "Supabase query failed",
                "details": (
                    response.error.message
                    if response.error.message
                    else str(response.error)
                ),
            }
        return {"status": "success", "orders": response.data or []}
    except Exception as e:
        return {"error": f"Unexpected error fetching purchases: {str(e)}"}


# -------------------- Spending Limit Tools --------------------
@mcp_server.tool()
async def set_spending_limit(user_identifier: str, limit_value: float) -> dict:
    """Sets the daily spending limit for a user."""
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        return {"error": "Supabase URL or service role key not configured."}
    try:
        sb = create_client(supabase_url, supabase_key)
        resp = (
            sb.table("spending_limits")
            .upsert(
                {"user_identifier": user_identifier, "limit_value": limit_value},
                on_conflict="user_identifier",
            )
            .execute()
        )
        if hasattr(resp, "error") and resp.error:
            return {
                "error": "Failed to set spending limit",
                "details": resp.error.message,
            }
        return {
            "status": "success",
            "message": f"Spending limit set to {limit_value} for {user_identifier}.",
            "data": resp.data,
        }
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}


@mcp_server.tool()
async def get_spending_status(user_identifier: str) -> dict:
    """Retrieves the daily spending limit and current day's spending for a user."""
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        return {"error": "Supabase URL or service role key not configured."}
    try:
        sb = create_client(supabase_url, supabase_key)
        # Get limit
        limit_resp = (
            sb.table("spending_limits")
            .select("limit_value")
            .eq("user_identifier", user_identifier)
            .execute()
        )
        if hasattr(limit_resp, "error") and limit_resp.error:
            return {
                "error": "Failed to fetch spending limit",
                "details": limit_resp.error.message,
            }
        if limit_resp.data and len(limit_resp.data) > 0:
            limit_value = float(limit_resp.data[0].get("limit_value", 0))
        else:
            limit_value = 1e30  # default very high
        # Calculate today's spending
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        orders_resp = (
            sb.table("orders")
            .select("total_amount_value, total_amount_currency, created_at")
            .eq("user_identifier", user_identifier)
            .gte("created_at", today_start)
            .execute()
        )
        if hasattr(orders_resp, "error") and orders_resp.error:
            return {
                "error": "Failed to fetch today's orders",
                "details": orders_resp.error.message,
            }
        orders_today = orders_resp.data or []
        total_spent = sum(
            [float(o.get("total_amount_value", 0) or 0) for o in orders_today]
        )
        remaining = limit_value - total_spent
        # Advice for anti-retail therapy
        if total_spent >= limit_value:
            advice = "Whoa, you've hit or exceeded your daily spending limit! Time for some anti-retail therapy 🍵."
        elif total_spent >= 0.9 * limit_value:
            advice = "You're getting close to your daily limit—maybe take a breath before splurging more."
        else:
            advice = "All clear! You have room to spend today."
        return {
            "status": "success",
            "spending_limit": limit_value,
            "total_spent_today": total_spent,
            "remaining_limit": remaining,
            "transactions_today": orders_today,
            "advice": advice,
        }
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

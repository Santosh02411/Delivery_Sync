import React, { useEffect, useState } from "react";
import {
  fetchPublicStores,
  fetchStoreProducts,
  fetchMyCart,
  addToCart,
  updateCartItem,
  removeCartItem,
  checkoutCart,
  verifyPayment,
} from "../services/api";
import "../styles/auth.css";

/**
 * Loads Razorpay's real Checkout.js widget from their own CDN, once,
 * the first time it's actually needed — not on every render, and not
 * unconditionally on page load for customers who never shop.
 */
function loadRazorpayScript() {
  return new Promise((resolve) => {
    if (window.Razorpay) {
      resolve(true);
      return;
    }
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.onload = () => resolve(true);
    script.onerror = () => resolve(false);
    document.body.appendChild(script);
  });
}

export default function Storefront({ token, onOrderPlaced }) {
  const [stores, setStores] = useState([]);
  const [selectedStore, setSelectedStore] = useState(null);
  const [products, setProducts] = useState([]);
  const [cart, setCart] = useState({ items: [], subtotal: 0, org_id: null });
  const [isCartOpen, setIsCartOpen] = useState(false);
  const [isCheckingOut, setIsCheckingOut] = useState(false);
  const [addressLine, setAddressLine] = useState("");
  const [city, setCity] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);

  useEffect(() => {
    loadStores();
    loadCart();
  }, []);

  async function loadStores() {
    try {
      const data = await fetchPublicStores();
      setStores(data);
    } catch (err) {
      console.warn("Could not load stores:", err.message);
    }
  }

  async function loadCart() {
    try {
      const data = await fetchMyCart(token);
      setCart(data);
    } catch (err) {
      console.warn("Could not load cart:", err.message);
    }
  }

  async function openStore(store) {
    setSelectedStore(store);
    setError(null);
    try {
      const data = await fetchStoreProducts(store.id);
      setProducts(data);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleAddToCart(productId) {
    setError(null);
    try {
      const updatedCart = await addToCart(token, productId, 1);
      setCart(updatedCart);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleUpdateQuantity(itemId, quantity) {
    const updatedCart = await updateCartItem(token, itemId, quantity);
    setCart(updatedCart);
  }

  async function handleRemoveItem(itemId) {
    const updatedCart = await removeCartItem(token, itemId);
    setCart(updatedCart);
  }

  async function handleCheckout(e) {
    e.preventDefault();
    setError(null);
    setIsCheckingOut(true);
    try {
      const checkoutResp = await checkoutCart(token, addressLine.trim(), city.trim(), phone.trim());

      if (checkoutResp.is_test_mode) {
        // No Razorpay account configured — confirm immediately through
        // the clearly-labeled test-mode path instead of opening a real
        // payment widget. See services/payment.py for why this exists.
        await verifyPayment(token, { order_id: checkoutResp.order_id });
        setSuccessMessage("Order placed! (Test mode — no real payment gateway is connected yet.)");
        await loadCart();
        setIsCartOpen(false);
        if (onOrderPlaced) onOrderPlaced();
        return;
      }

      const scriptLoaded = await loadRazorpayScript();
      if (!scriptLoaded) {
        setError("Couldn't load the payment widget. Check your connection and try again.");
        return;
      }

      const razorpay = new window.Razorpay({
        key: checkoutResp.razorpay_key_id,
        amount: checkoutResp.amount_paise,
        currency: checkoutResp.currency,
        name: "Delivery Sync",
        description: selectedStore?.name || "Order",
        order_id: checkoutResp.razorpay_order_id,
        handler: async (response) => {
          try {
            await verifyPayment(token, {
              order_id: checkoutResp.order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
            });
            setSuccessMessage("Payment successful! Your order has been placed.");
            await loadCart();
            setIsCartOpen(false);
            if (onOrderPlaced) onOrderPlaced();
          } catch (err) {
            setError("Payment succeeded but confirming your order failed: " + err.message);
          }
        },
        prefill: { contact: phone },
        theme: { color: "#f2a93b" },
      });
      razorpay.open();
    } catch (err) {
      setError(err.message);
    } finally {
      setIsCheckingOut(false);
    }
  }

  if (successMessage) {
    return (
      <div className="card" style={{ maxWidth: "480px", margin: "24px auto", textAlign: "center" }}>
        <div style={{ fontSize: "32px", marginBottom: "8px" }}>✅</div>
        <p style={{ fontSize: "14px" }}>{successMessage}</p>
        <button className="btn btn-primary" onClick={() => { setSuccessMessage(null); setSelectedStore(null); }}>
          Continue Shopping
        </button>
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <h3 style={{ margin: 0, fontSize: "16px" }}>
          {selectedStore ? selectedStore.name : "Browse Stores"}
        </h3>
        <button className="btn" onClick={() => setIsCartOpen(!isCartOpen)}>
          🛒 Cart {cart.items.length > 0 && `(${cart.items.length})`}
        </button>
      </div>

      {error && <p style={{ color: "var(--danger)", fontSize: "12.5px" }}>{error}</p>}

      {isCartOpen && (
        <div className="card" style={{ marginBottom: "16px" }}>
          <strong style={{ fontSize: "13.5px" }}>Your Cart</strong>
          {cart.items.length === 0 && (
            <p style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>Your cart is empty.</p>
          )}
          {cart.items.map((line) => (
            <div key={line.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderBottom: "1px solid var(--border-color)" }}>
              <div>
                <div style={{ fontSize: "13px" }}>{line.product.name}</div>
                <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>₹{line.product.price.toFixed(2)} each</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <button className="btn" style={{ padding: "2px 8px", fontSize: "12px" }} onClick={() => handleUpdateQuantity(line.id, line.quantity - 1)}>-</button>
                <span style={{ fontSize: "13px" }}>{line.quantity}</span>
                <button className="btn" style={{ padding: "2px 8px", fontSize: "12px" }} onClick={() => handleUpdateQuantity(line.id, line.quantity + 1)}>+</button>
                <button className="btn" style={{ padding: "2px 8px", fontSize: "11px", color: "var(--danger)" }} onClick={() => handleRemoveItem(line.id)}>✕</button>
              </div>
            </div>
          ))}
          {cart.items.length > 0 && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: "10px", fontWeight: 600, fontSize: "14px" }}>
                <span>Subtotal</span>
                <span>₹{cart.subtotal.toFixed(2)}</span>
              </div>
              <form onSubmit={handleCheckout} style={{ marginTop: "12px" }}>
                <div className="auth-field">
                  <label>Delivery Address</label>
                  <input className="input" type="text" value={addressLine} onChange={(e) => setAddressLine(e.target.value)} required />
                </div>
                <div className="auth-field">
                  <label>City</label>
                  <input className="input" type="text" value={city} onChange={(e) => setCity(e.target.value)} />
                </div>
                <div className="auth-field">
                  <label>Phone</label>
                  <input className="input" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} required />
                </div>
                <button type="submit" className="btn btn-primary" disabled={isCheckingOut}>
                  {isCheckingOut ? "Processing..." : `Pay ₹${cart.subtotal.toFixed(2)}`}
                </button>
              </form>
            </>
          )}
        </div>
      )}

      {!selectedStore && (
        <div style={{ display: "grid", gap: "10px" }}>
          {stores.map((store) => (
            <div key={store.id} className="card" style={{ cursor: "pointer" }} onClick={() => openStore(store)}>
              <strong style={{ fontSize: "14px" }}>{store.name}</strong>
            </div>
          ))}
          {stores.length === 0 && (
            <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>No stores are open for shopping yet.</p>
          )}
        </div>
      )}

      {selectedStore && (
        <div>
          <button className="btn" style={{ marginBottom: "12px", fontSize: "12px" }} onClick={() => setSelectedStore(null)}>
            ← All Stores
          </button>
          <div style={{ display: "grid", gap: "10px" }}>
            {products.map((product) => (
              <div key={product.id} className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <strong style={{ fontSize: "14px" }}>{product.name}</strong>
                  {product.description && (
                    <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{product.description}</div>
                  )}
                  <div style={{ fontSize: "13px", color: "var(--accent)", fontWeight: 600 }}>₹{product.price.toFixed(2)}</div>
                </div>
                <button className="btn btn-primary" style={{ fontSize: "12px" }} onClick={() => handleAddToCart(product.id)}>
                  Add
                </button>
              </div>
            ))}
            {products.length === 0 && (
              <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>No products available right now.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

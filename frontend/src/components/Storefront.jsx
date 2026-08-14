import React, { useEffect, useState } from "react";
import {
  fetchPublicStores,
  fetchStoreProducts,
  verifyPayment,
  checkoutCart,
  validateCoupon,
  fetchDeliverySlots,
  API_BASE_URL,
} from "../services/api";
import {
  cachePublicStores,
  getCachedPublicStores,
  cacheStoreProducts,
  getCachedStoreProducts,
  getLocalCart,
  addToLocalCart,
  updateLocalCartQuantity,
  removeFromLocalCart,
  clearLocalCart,
  setPendingCheckout,
  getPendingCheckout,
  clearPendingCheckout,
} from "../services/customerOfflineStore";
import { startCartAutoSync } from "../services/cartSyncEngine";
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

// The local cart's line shape is { product_id, org_id, product, quantity }.
function cartSubtotal(items) {
  return items.reduce((sum, line) => sum + line.product.price * line.quantity, 0);
}

// Mirrors the backend's exact pricing formula (routes/checkout.py) so the
// cart can show a live preview before the checkout API call happens.
function computeBreakdownPreview(subtotal, appliedCoupon, store) {
  const discountAmount = appliedCoupon ? appliedCoupon.discount_amount : 0;
  const deliveryFee = store?.delivery_fee || 0;
  const taxRatePercent = store?.tax_rate_percent || 0;
  const taxableAmount = Math.max(subtotal - discountAmount, 0);
  const taxAmount = Math.round(taxableAmount * (taxRatePercent / 100) * 100) / 100;
  const total = Math.round((taxableAmount + taxAmount + deliveryFee) * 100) / 100;
  return { subtotal, discountAmount, deliveryFee, taxAmount, taxRatePercent, total };
}

// image_url from the backend is a relative path like "/uploads/products/xyz.jpg" —
// resolve it against the API host so <img> tags actually load it.
function resolveImageUrl(imageUrl) {
  if (!imageUrl) return null;
  if (/^https?:\/\//i.test(imageUrl)) return imageUrl;
  return `${API_BASE_URL}${imageUrl}`;
}

export default function Storefront({ token, onOrderPlaced }) {
  const [stores, setStores] = useState([]);
  const [selectedStore, setSelectedStore] = useState(null);
  const [products, setProducts] = useState([]);
  const [cartItems, setCartItems] = useState([]);
  const [isCartOpen, setIsCartOpen] = useState(false);
  const [isCheckingOut, setIsCheckingOut] = useState(false);
  const [addressLine, setAddressLine] = useState("");
  const [city, setCity] = useState("");
  const [phone, setPhone] = useState("");
  const [couponCode, setCouponCode] = useState("");
  const [appliedCoupon, setAppliedCoupon] = useState(null);
  const [isApplyingCoupon, setIsApplyingCoupon] = useState(false);
  const [couponError, setCouponError] = useState(null);
  const [availableDates, setAvailableDates] = useState([]);
  const [selectedSlotDate, setSelectedSlotDate] = useState(null);
  const [slotOptions, setSlotOptions] = useState([]);
  const [selectedSlotStart, setSelectedSlotStart] = useState(null);
  const [isLoadingSlots, setIsLoadingSlots] = useState(false);
  const [slotError, setSlotError] = useState(null);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);
  const [isOffline, setIsOffline] = useState(false);
  const [pendingCheckout, setPendingCheckoutState] = useState(null);

  useEffect(() => {
    loadStores();
    loadLocalCart();
    checkPendingCheckout();

    const stopCartSync = startCartAutoSync(token, (result) => {
      if (result.completed) {
        setSuccessMessage(
          result.testMode
            ? "Your queued order was placed automatically once you were back online! (Test mode)"
            : "Your queued order was placed automatically once you were back online!"
        );
        setPendingCheckoutState(null);
        loadLocalCart();
        if (onOrderPlaced) onOrderPlaced();
      } else if (result.readyToPay) {
        checkPendingCheckout();
      } else if (result.failed) {
        setError(`Your queued order couldn't be placed: ${result.message}`);
        setPendingCheckoutState(null);
      }
    });

    const handleOnline = () => setIsOffline(false);
    const handleOffline = () => setIsOffline(true);
    setIsOffline(!navigator.onLine);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      stopCartSync();
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  async function checkPendingCheckout() {
    const pending = await getPendingCheckout();
    setPendingCheckoutState(pending);
  }

  async function loadStores() {
    try {
      const data = await fetchPublicStores();
      setStores(data);
      await cachePublicStores(data);
    } catch (err) {
      // Offline / unreachable — fall back to whatever was cached last time.
      const cached = await getCachedPublicStores();
      setStores(cached);
    }
  }

  async function loadLocalCart() {
    const items = await getLocalCart();
    setCartItems(items);
  }

  async function openStore(store) {
    setSelectedStore(store);
    setError(null);
    try {
      const data = await fetchStoreProducts(store.id);
      setProducts(data);
      await cacheStoreProducts(store.id, data);
    } catch (err) {
      const cached = await getCachedStoreProducts(store.id);
      setProducts(cached);
      if (cached.length === 0) setError("No cached products for this store, and you're offline.");
    }

    // Delivery slot picker: next 7 days (today included), default to today.
    const dates = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date();
      d.setDate(d.getDate() + i);
      dates.push(d.toISOString().slice(0, 10));
    }
    setAvailableDates(dates);
    setSelectedSlotDate(dates[0]);
    setSelectedSlotStart(null);
    await loadSlotsForDate(store.id, dates[0]);
  }

  async function loadSlotsForDate(orgId, dateStr) {
    setIsLoadingSlots(true);
    setSlotError(null);
    try {
      setSlotOptions(await fetchDeliverySlots(orgId, dateStr));
    } catch (err) {
      setSlotOptions([]);
      setSlotError(err instanceof TypeError ? "Delivery slots need a connection to load." : err.message);
    } finally {
      setIsLoadingSlots(false);
    }
  }

  function handleSlotDateChange(dateStr) {
    setSelectedSlotDate(dateStr);
    setSelectedSlotStart(null);
    loadSlotsForDate(selectedStore.id, dateStr);
  }

  function formatSlotTime(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function formatSlotDateLabel(dateStr) {
    const d = new Date(dateStr + "T00:00:00");
    const today = new Date().toISOString().slice(0, 10);
    const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    if (dateStr === today) return "Today";
    if (dateStr === tomorrow) return "Tomorrow";
    return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  }

  async function handleAddToCart(product) {
    setError(null);
    await addToLocalCart(product, 1);
    await loadLocalCart();
  }

  async function handleUpdateQuantity(productId, quantity) {
    const line = cartItems.find((l) => l.product_id === productId);
    if (line && line.product.stock_quantity !== null && line.product.stock_quantity !== undefined && quantity > line.product.stock_quantity) {
      setError(`Only ${line.product.stock_quantity} left of "${line.product.name}".`);
      return;
    }
    setError(null);
    await updateLocalCartQuantity(productId, quantity);
    await loadLocalCart();
  }

  async function handleRemoveItem(productId) {
    await removeFromLocalCart(productId);
    await loadLocalCart();
  }

  async function completeRealPayment(checkoutResp) {
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
          await clearPendingCheckout();
          setPendingCheckoutState(null);
          await clearLocalCart();
          await loadLocalCart();
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
  }

  async function handleApplyCoupon(e) {
    e.preventDefault();
    if (!couponCode.trim()) return;
    setCouponError(null);
    setIsApplyingCoupon(true);
    try {
      const result = await validateCoupon(token, couponCode.trim());
      setAppliedCoupon(result);
    } catch (err) {
      setAppliedCoupon(null);
      setCouponError(err.message);
    } finally {
      setIsApplyingCoupon(false);
    }
  }

  function handleRemoveCoupon() {
    setAppliedCoupon(null);
    setCouponCode("");
    setCouponError(null);
  }

  async function handleCheckout(e) {
    e.preventDefault();
    setError(null);
    setIsCheckingOut(true);

    const details = {
      address_line: addressLine.trim(),
      city: city.trim(),
      phone: phone.trim(),
      coupon_code: appliedCoupon ? appliedCoupon.code : null,
      slot_start: selectedSlotStart,
    };

    try {
      if (!navigator.onLine) {
        // Can't pay with no connection — queue the intent instead of
        // failing outright. The cart stays exactly as-is locally; the
        // sync engine will pick this up and complete the order (or
        // prompt for payment) automatically once back online.
        await setPendingCheckout(details);
        setPendingCheckoutState(await getPendingCheckout());
        setError(null);
        setSuccessMessage("You're offline — your order is queued and will be placed automatically once you're back online.");
        return;
      }

      const checkoutResp = await checkoutCart(token, details.address_line, details.city, details.phone, details.coupon_code, details.slot_start);

      if (checkoutResp.is_test_mode) {
        await verifyPayment(token, { order_id: checkoutResp.order_id });
        setSuccessMessage("Order placed! (Test mode — no real payment gateway is connected yet.)");
        await clearLocalCart();
        await loadLocalCart();
        setIsCartOpen(false);
        setAppliedCoupon(null);
        setCouponCode("");
        setSelectedSlotStart(null);
        if (onOrderPlaced) onOrderPlaced();
        return;
      }

      await completeRealPayment(checkoutResp);
    } catch (err) {
      if (err instanceof TypeError) {
        // The request itself failed to reach the server (e.g. connection
        // dropped mid-attempt) — treat exactly like the offline case above.
        await setPendingCheckout(details);
        setPendingCheckoutState(await getPendingCheckout());
        setSuccessMessage("You're offline — your order is queued and will be placed automatically once you're back online.");
      } else {
        setError(err.message);
      }
    } finally {
      setIsCheckingOut(false);
    }
  }

  async function handlePayNow() {
    setError(null);
    setIsCheckingOut(true);
    try {
      const checkoutResp = await checkoutCart(token, pendingCheckout.address_line, pendingCheckout.city, pendingCheckout.phone, pendingCheckout.coupon_code, pendingCheckout.slot_start);
      if (checkoutResp.is_test_mode) {
        await verifyPayment(token, { order_id: checkoutResp.order_id });
        setSuccessMessage("Order placed! (Test mode)");
        await clearPendingCheckout();
        setPendingCheckoutState(null);
        await clearLocalCart();
        await loadLocalCart();
        if (onOrderPlaced) onOrderPlaced();
      } else {
        await completeRealPayment(checkoutResp);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setIsCheckingOut(false);
    }
  }

  const subtotal = cartSubtotal(cartItems);

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
          🛒 Cart {cartItems.length > 0 && `(${cartItems.length})`}
        </button>
      </div>

      {isOffline && (
        <div className="connectivity-banner offline" style={{ marginBottom: "16px" }}>
          Offline — browsing cached products. Your cart still works; checkout will complete once you're back online.
        </div>
      )}

      {pendingCheckout && (
        <div className="card" style={{ marginBottom: "16px", borderColor: "var(--accent)" }}>
          <strong style={{ fontSize: "13px" }}>Order queued while offline</strong>
          <p style={{ fontSize: "12px", color: "var(--text-secondary)", margin: "4px 0" }}>
            Queued at {new Date(pendingCheckout.queued_at).toLocaleString()} — deliver to {pendingCheckout.address_line}
          </p>
          {navigator.onLine && (
            <button className="btn btn-primary" style={{ fontSize: "12px" }} onClick={handlePayNow} disabled={isCheckingOut}>
              {isCheckingOut ? "Processing..." : "Complete Payment Now"}
            </button>
          )}
        </div>
      )}

      {error && <p style={{ color: "var(--danger)", fontSize: "12.5px" }}>{error}</p>}

      {isCartOpen && (
        <div className="card" style={{ marginBottom: "16px" }}>
          <strong style={{ fontSize: "13.5px" }}>Your Cart</strong>
          {cartItems.length === 0 && (
            <p style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>Your cart is empty.</p>
          )}
          {cartItems.map((line) => (
            <div key={line.product_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderBottom: "1px solid var(--border-color)" }}>
              <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                {line.product.image_url && (
                  <img
                    src={resolveImageUrl(line.product.image_url)}
                    alt={line.product.name}
                    style={{ width: "36px", height: "36px", objectFit: "cover", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-color)" }}
                  />
                )}
                <div>
                  <div style={{ fontSize: "13px" }}>{line.product.name}</div>
                  <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>₹{line.product.price.toFixed(2)} each</div>
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <button className="btn" style={{ padding: "2px 8px", fontSize: "12px" }} onClick={() => handleUpdateQuantity(line.product_id, line.quantity - 1)}>-</button>
                <span style={{ fontSize: "13px" }}>{line.quantity}</span>
                <button className="btn" style={{ padding: "2px 8px", fontSize: "12px" }} onClick={() => handleUpdateQuantity(line.product_id, line.quantity + 1)}>+</button>
                <button className="btn" style={{ padding: "2px 8px", fontSize: "11px", color: "var(--danger)" }} onClick={() => handleRemoveItem(line.product_id)}>✕</button>
              </div>
            </div>
          ))}
          {cartItems.length > 0 && (
            <>
              <div style={{ marginTop: "12px" }}>
                {!appliedCoupon ? (
                  <form onSubmit={handleApplyCoupon} style={{ display: "flex", gap: "6px" }}>
                    <input
                      className="input"
                      type="text"
                      placeholder="Coupon code"
                      value={couponCode}
                      onChange={(e) => setCouponCode(e.target.value)}
                      style={{ flex: 1 }}
                    />
                    <button type="submit" className="btn" style={{ fontSize: "12px" }} disabled={isApplyingCoupon || !couponCode.trim()}>
                      {isApplyingCoupon ? "Checking..." : "Apply"}
                    </button>
                  </form>
                ) : (
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "12.5px", color: "var(--accent)" }}>
                    <span>"{appliedCoupon.code}" applied — ₹{appliedCoupon.discount_amount.toFixed(2)} off</span>
                    <button className="btn" style={{ fontSize: "11px", padding: "2px 6px" }} onClick={handleRemoveCoupon}>Remove</button>
                  </div>
                )}
                {couponError && <p style={{ color: "var(--danger)", fontSize: "12px", marginTop: "4px" }}>{couponError}</p>}
              </div>

              {(() => {
                const breakdown = computeBreakdownPreview(subtotal, appliedCoupon, selectedStore);
                return (
                  <div style={{ marginTop: "12px", fontSize: "13px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>Subtotal</span>
                      <span>₹{breakdown.subtotal.toFixed(2)}</span>
                    </div>
                    {breakdown.discountAmount > 0 && (
                      <div style={{ display: "flex", justifyContent: "space-between", color: "var(--accent)" }}>
                        <span>Discount</span>
                        <span>-₹{breakdown.discountAmount.toFixed(2)}</span>
                      </div>
                    )}
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>Delivery fee</span>
                      <span>₹{breakdown.deliveryFee.toFixed(2)}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>GST ({breakdown.taxRatePercent}%)</span>
                      <span>₹{breakdown.taxAmount.toFixed(2)}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, fontSize: "14px", marginTop: "6px", paddingTop: "6px", borderTop: "1px solid var(--border-color)" }}>
                      <span>Total</span>
                      <span>₹{breakdown.total.toFixed(2)}</span>
                    </div>
                  </div>
                );
              })()}

              <form onSubmit={handleCheckout} style={{ marginTop: "12px" }}>
                <div className="auth-field">
                  <label>Delivery Window (optional — leave unset for ASAP)</label>
                  <div style={{ display: "flex", gap: "6px", overflowX: "auto", paddingBottom: "4px", marginBottom: "8px" }}>
                    {availableDates.map((d) => (
                      <button
                        type="button"
                        key={d}
                        className="btn"
                        style={{
                          fontSize: "11.5px",
                          padding: "4px 8px",
                          flexShrink: 0,
                          background: selectedSlotDate === d ? "var(--accent)" : undefined,
                          color: selectedSlotDate === d ? "white" : undefined,
                        }}
                        onClick={() => handleSlotDateChange(d)}
                      >
                        {formatSlotDateLabel(d)}
                      </button>
                    ))}
                  </div>
                  {isLoadingSlots ? (
                    <p style={{ fontSize: "12px", color: "var(--text-secondary)" }}>Loading slots...</p>
                  ) : slotError ? (
                    <p style={{ fontSize: "12px", color: "var(--danger)" }}>{slotError}</p>
                  ) : slotOptions.length === 0 ? (
                    <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>No delivery windows left for this day.</p>
                  ) : (
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                      {slotOptions.map((s) => (
                        <button
                          type="button"
                          key={s.start}
                          className="btn"
                          disabled={!s.available}
                          title={s.available ? `${s.remaining} of ${s.capacity} left` : "Full"}
                          style={{
                            fontSize: "11.5px",
                            padding: "4px 8px",
                            opacity: s.available ? 1 : 0.4,
                            background: selectedSlotStart === s.start ? "var(--accent)" : undefined,
                            color: selectedSlotStart === s.start ? "white" : undefined,
                          }}
                          onClick={() => setSelectedSlotStart(selectedSlotStart === s.start ? null : s.start)}
                        >
                          {formatSlotTime(s.start)}–{formatSlotTime(s.end)}
                        </button>
                      ))}
                    </div>
                  )}
                  {selectedSlotStart && (
                    <p style={{ fontSize: "11.5px", color: "var(--accent)", marginTop: "4px" }}>
                      Delivering {formatSlotDateLabel(selectedSlotDate)}, {formatSlotTime(selectedSlotStart)}
                    </p>
                  )}
                </div>
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
                  {isCheckingOut
                    ? "Processing..."
                    : navigator.onLine
                    ? `Pay ₹${computeBreakdownPreview(subtotal, appliedCoupon, selectedStore).total.toFixed(2)}`
                    : `Place Order (₹${computeBreakdownPreview(subtotal, appliedCoupon, selectedStore).total.toFixed(2)}, will complete when online)`}
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
            {products.map((product) => {
              const inCartQty = cartItems.find((l) => l.product_id === product.id)?.quantity || 0;
              const isOutOfStock = product.stock_quantity !== null && product.stock_quantity !== undefined && product.stock_quantity <= 0;
              const isAtStockLimit = product.stock_quantity !== null && product.stock_quantity !== undefined && inCartQty >= product.stock_quantity;
              return (
                <div key={product.id} className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", opacity: isOutOfStock ? 0.6 : 1 }}>
                  <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
                    {product.image_url ? (
                      <img
                        src={resolveImageUrl(product.image_url)}
                        alt={product.name}
                        style={{ width: "56px", height: "56px", objectFit: "cover", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-color)", flexShrink: 0 }}
                      />
                    ) : (
                      <div style={{ width: "56px", height: "56px", borderRadius: "var(--radius-sm)", border: "1px dashed var(--border-color)", flexShrink: 0 }} />
                    )}
                    <div>
                      <strong style={{ fontSize: "14px" }}>{product.name}</strong>
                      {product.description && (
                        <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{product.description}</div>
                      )}
                      <div style={{ fontSize: "13px", color: "var(--accent)", fontWeight: 600 }}>₹{product.price.toFixed(2)}</div>
                      {product.review_count > 0 ? (
                        <div style={{ fontSize: "11.5px", color: "var(--text-secondary)" }}>
                          ★ {product.average_rating} ({product.review_count} review{product.review_count === 1 ? "" : "s"})
                        </div>
                      ) : (
                        <div style={{ fontSize: "11.5px", color: "var(--text-muted)" }}>No reviews yet</div>
                      )}
                      {isOutOfStock ? (
                        <div style={{ fontSize: "11.5px", color: "var(--danger)", fontWeight: 600 }}>Out of stock</div>
                      ) : product.stock_quantity !== null && product.stock_quantity !== undefined && product.stock_quantity <= 5 ? (
                        <div style={{ fontSize: "11.5px", color: "var(--danger)" }}>Only {product.stock_quantity} left</div>
                      ) : null}
                    </div>
                  </div>
                  <button
                    className="btn btn-primary"
                    style={{ fontSize: "12px" }}
                    onClick={() => handleAddToCart(product)}
                    disabled={isOutOfStock || isAtStockLimit}
                  >
                    {isOutOfStock ? "Sold out" : isAtStockLimit ? "Max in cart" : "Add"}
                  </button>
                </div>
              );
            })}
            {products.length === 0 && (
              <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>No products available right now.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

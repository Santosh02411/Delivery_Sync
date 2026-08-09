import React, { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import {
  fetchMyProducts,
  createProduct,
  updateProduct,
  deleteProduct,
  setStoreVisibility,
  fetchMyOrganization,
} from "../services/api";
import "../styles/auth.css";

export default function ProductManager() {
  const { token, user } = useAuth();
  const [products, setProducts] = useState([]);
  const [isPublic, setIsPublic] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [price, setPrice] = useState("");
  const [category, setCategory] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    load();
    if (user.role === "admin") loadOrgVisibility();
  }, []);

  async function loadOrgVisibility() {
    try {
      const org = await fetchMyOrganization(token);
      setIsPublic(!!org.is_public_store);
    } catch (err) {
      console.warn("Could not load store visibility:", err.message);
    }
  }

  async function load() {
    try {
      const data = await fetchMyProducts(token);
      setProducts(data);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleAdd(e) {
    e.preventDefault();
    setError(null);
    try {
      await createProduct(token, {
        name: name.trim(),
        description: description.trim() || null,
        price: parseFloat(price),
        category: category.trim() || null,
      });
      setName("");
      setDescription("");
      setPrice("");
      setCategory("");
      setIsAdding(false);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleToggleActive(product) {
    await updateProduct(token, product.id, { is_active: !product.is_active });
    await load();
  }

  async function handleDelete(productId) {
    if (!window.confirm("Delete this product? This can't be undone.")) return;
    await deleteProduct(token, productId);
    await load();
  }

  async function handleToggleStore() {
    const next = !isPublic;
    setIsPublic(next);
    try {
      await setStoreVisibility(token, next);
    } catch (err) {
      setIsPublic(!next); // revert on failure
      setError(err.message);
    }
  }

  return (
    <div>
      <h2 className="page-title">Products</h2>

      {user.role === "admin" && (
        <div className="card" style={{ marginBottom: "20px", maxWidth: "500px" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "13.5px", cursor: "pointer" }}>
            <input type="checkbox" checked={isPublic} onChange={handleToggleStore} />
            <div>
              <strong>Public Storefront</strong>
              <div style={{ fontSize: "12px", color: "var(--text-secondary)", fontWeight: 400 }}>
                When on, customers can browse and buy from your active
                products at the public store list.
              </div>
            </div>
          </label>
        </div>
      )}

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      <button className="btn btn-primary" style={{ marginBottom: "16px" }} onClick={() => setIsAdding(!isAdding)}>
        {isAdding ? "Cancel" : "+ Add Product"}
      </button>

      {isAdding && (
        <form onSubmit={handleAdd} className="card" style={{ maxWidth: "420px", marginBottom: "20px" }}>
          <div className="auth-field">
            <label>Name</label>
            <input className="input" type="text" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="auth-field">
            <label>Description</label>
            <input className="input" type="text" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div className="auth-field">
            <label>Price (₹)</label>
            <input className="input" type="number" step="0.01" min="0" value={price} onChange={(e) => setPrice(e.target.value)} required />
          </div>
          <div className="auth-field">
            <label>Category</label>
            <input className="input" type="text" value={category} onChange={(e) => setCategory(e.target.value)} />
          </div>
          <button type="submit" className="btn btn-primary">Save Product</button>
        </form>
      )}

      <div style={{ display: "grid", gap: "10px" }}>
        {products.map((p) => (
          <div key={p.id} className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", opacity: p.is_active ? 1 : 0.5 }}>
            <div>
              <strong style={{ fontSize: "14px" }}>{p.name}</strong>
              {p.category && <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}> · {p.category}</span>}
              <div style={{ fontSize: "13px", color: "var(--accent)", fontWeight: 600 }}>₹{p.price.toFixed(2)}</div>
              {p.description && <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{p.description}</div>}
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <button className="btn" style={{ fontSize: "12px" }} onClick={() => handleToggleActive(p)}>
                {p.is_active ? "Deactivate" : "Activate"}
              </button>
              <button className="btn" style={{ fontSize: "12px", color: "var(--danger)" }} onClick={() => handleDelete(p.id)}>
                Delete
              </button>
            </div>
          </div>
        ))}
        {products.length === 0 && !isAdding && (
          <p style={{ color: "var(--text-secondary)" }}>No products yet — add your first one above.</p>
        )}
      </div>
    </div>
  );
}

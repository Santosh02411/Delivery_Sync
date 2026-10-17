import React, { useState, useRef } from "react";
import { parseCSV } from "../services/csvParser";
import { bulkImportProducts } from "../services/api";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

export const MAX_IMPORT_ROWS = 1000;

const TEMPLATE_CSV = `name,price,description,category,image_url,stock_quantity,cost_price,is_active
Basmati Rice 5kg,649,Aged long-grain rice,Grocery,,40,520,yes
Toor Dal 1kg,159.50,Unpolished,Grocery,https://example.com/toor-dal.jpg,,,yes
`;

// People name columns all sorts of ways in spreadsheets; map the common ones onto ours.
const HEADER_ALIASES = {
  name: "name", product: "name", product_name: "name", title: "name", item: "name", item_name: "name",
  price: "price", mrp: "price", selling_price: "price", sale_price: "price", rate: "price",
  description: "description", details: "description",
  category: "category", type: "category",
  image_url: "image_url", image: "image_url", image_link: "image_url", photo: "image_url", picture: "image_url",
  stock_quantity: "stock_quantity", stock: "stock_quantity", quantity: "stock_quantity", qty: "stock_quantity", inventory: "stock_quantity",
  cost_price: "cost_price", cost: "cost_price", purchase_price: "cost_price",
  is_active: "is_active", active: "is_active", status: "is_active",
};

function normalizeHeader(header) {
  const key = header.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  return HEADER_ALIASES[key] || null;
}

/** Re-keys parsed CSV rows onto our column names; returns { rows, ignoredColumns }. */
export function mapColumns(rawRows) {
  const headers = Object.keys(rawRows[0] || {});
  const mapping = {};
  const ignoredColumns = [];
  headers.forEach((h) => {
    const target = normalizeHeader(h);
    if (target && !Object.values(mapping).includes(target)) mapping[h] = target;
    else ignoredColumns.push(h);
  });
  const rows = rawRows.map((raw) => {
    const row = {};
    Object.entries(mapping).forEach(([from, to]) => { row[to] = (raw[from] ?? "").trim(); });
    return row;
  });
  return { rows, ignoredColumns, mapped: Object.values(mapping) };
}

function looksLikeNumber(value) {
  const cleaned = String(value).replace(/[,₹$\s]/g, "").replace(/^rs\.?/i, "");
  return cleaned !== "" && !Number.isNaN(Number(cleaned));
}

/** Quick pre-check so obvious problems are visible BEFORE uploading (the server re-validates everything). */
export function findRowProblems(rows) {
  const problems = [];
  rows.forEach((row, i) => {
    if (!row.name) problems.push({ row: i + 1, message: "name is empty" });
    else if (!row.price) problems.push({ row: i + 1, message: `"${row.name}" has no price` });
    else if (!looksLikeNumber(row.price)) problems.push({ row: i + 1, message: `"${row.name}": price "${row.price}" isn't a number` });
  });
  return problems;
}

/**
 * Bulk-add (or refresh) products from a CSV. Mirrors the delivery CSV import: preview first,
 * then a per-row report afterwards — a bad row never blocks the good ones (see
 * backend routes/products.py bulk_import_products).
 */
export default function ProductImportPanel({ onImportComplete, onClose }) {
  const { token } = useAuth();
  const { showToast } = useToast();
  const fileInputRef = useRef(null);
  const [fileName, setFileName] = useState("");
  const [rows, setRows] = useState([]);
  const [ignoredColumns, setIgnoredColumns] = useState([]);
  const [parseError, setParseError] = useState(null);
  const [updateExisting, setUpdateExisting] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [results, setResults] = useState(null);

  const problems = findRowProblems(rows);

  function reset() {
    setFileName(""); setRows([]); setIgnoredColumns([]); setParseError(null); setResults(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleFile(e) {
    const file = e.target.files[0];
    if (!file) return;
    reset();
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const raw = parseCSV(event.target.result);
        if (raw.length === 0) return setParseError("The file has no data rows (or couldn't be read as CSV).");
        const mapped = mapColumns(raw);
        const missing = ["name", "price"].filter((c) => !mapped.mapped.includes(c));
        if (missing.length) {
          return setParseError(
            `Missing required column(s): ${missing.join(", ")}. Found: ${Object.keys(raw[0]).join(", ")}. ` +
            `Use "Download template" to see the expected layout.`
          );
        }
        if (mapped.rows.length > MAX_IMPORT_ROWS) {
          return setParseError(`This file has ${mapped.rows.length} rows; the limit is ${MAX_IMPORT_ROWS} per import. Split it into smaller files.`);
        }
        setRows(mapped.rows);
        setIgnoredColumns(mapped.ignoredColumns);
      } catch (err) {
        setParseError(`Could not parse this file as CSV: ${err.message}`);
      }
    };
    reader.onerror = () => setParseError("Could not read the selected file.");
    reader.readAsText(file);
  }

  function downloadTemplate() {
    const url = URL.createObjectURL(new Blob([TEMPLATE_CSV], { type: "text/csv" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "products-template.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function handleSubmit() {
    if (rows.length === 0) return;
    setIsSubmitting(true);
    setResults(null);
    try {
      const response = await bulkImportProducts(token, rows, updateExisting);
      setResults(response);
      const done = response.created_count + response.updated_count;
      if (done > 0) {
        showToast(
          `${response.created_count} added, ${response.updated_count} updated` + (response.failure_count ? `, ${response.failure_count} failed.` : "."),
          response.failure_count ? "info" : "success"
        );
        onImportComplete();
      } else {
        showToast("No products were imported — check the errors below.", "error");
      }
    } catch (err) {
      showToast(`Product import failed: ${err.message}`, "error");
    } finally {
      setIsSubmitting(false);
    }
  }

  const failures = results ? results.results.filter((r) => !r.success) : [];

  return (
    <div className="card" style={{ marginBottom: "20px", maxWidth: "760px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
        <h3 style={{ margin: 0 }}>Import Products from CSV</h3>
        <button className="btn" style={{ fontSize: "12px", padding: "4px 10px" }} onClick={onClose}>Close</button>
      </div>
      <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", marginBottom: "12px" }}>
        Required columns: <code className="mono">name</code>, <code className="mono">price</code>. Optional:{" "}
        <code className="mono">description</code>, <code className="mono">category</code>,{" "}
        <code className="mono">image_url</code>, <code className="mono">stock_quantity</code>,{" "}
        <code className="mono">cost_price</code>, <code className="mono">is_active</code> (yes/no). Up to {MAX_IMPORT_ROWS} products per file.
      </p>

      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center", marginBottom: "12px" }}>
        <button className="btn" onClick={downloadTemplate}>⬇ Download template</button>
        <input ref={fileInputRef} type="file" accept=".csv,text/csv" onChange={handleFile} aria-label="Choose a CSV file of products" />
      </div>

      {parseError && <p role="alert" style={{ color: "var(--danger)", fontSize: "12.5px" }}>{parseError}</p>}

      {rows.length > 0 && !results && (
        <>
          <p style={{ fontSize: "13px", marginBottom: "6px" }}>
            <strong>{fileName}</strong>: {rows.length} product{rows.length === 1 ? "" : "s"} found.
          </p>
          {ignoredColumns.length > 0 && (
            <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>Ignored columns: {ignoredColumns.join(", ")}</p>
          )}
          {problems.length > 0 && (
            <div role="alert" style={{ fontSize: "12.5px", color: "var(--danger)", marginBottom: "8px" }}>
              {problems.length} row{problems.length === 1 ? "" : "s"} will be skipped:
              <ul style={{ margin: "4px 0 0 18px" }}>
                {problems.slice(0, 5).map((p) => <li key={p.row}>Row {p.row}: {p.message}</li>)}
                {problems.length > 5 && <li>…and {problems.length - 5} more</li>}
              </ul>
            </div>
          )}
          <div style={{ overflowX: "auto", marginBottom: "10px" }}>
            <table className="data-table" style={{ width: "100%", fontSize: "12.5px" }}>
              <thead><tr><th>#</th><th>Name</th><th>Price</th><th>Category</th><th>Stock</th></tr></thead>
              <tbody>
                {rows.slice(0, 8).map((r, i) => (
                  <tr key={i}><td>{i + 1}</td><td>{r.name}</td><td>{r.price}</td><td>{r.category || ""}</td><td>{r.stock_quantity || ""}</td></tr>
                ))}
              </tbody>
            </table>
            {rows.length > 8 && <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>…and {rows.length - 8} more rows</p>}
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12.5px", marginBottom: "12px" }}>
            <input type="checkbox" checked={updateExisting} onChange={(e) => setUpdateExisting(e.target.checked)} />
            Update products that already exist (matched by name) instead of skipping them
          </label>
          <div style={{ display: "flex", gap: "8px" }}>
            <button className="btn btn-primary" onClick={handleSubmit} disabled={isSubmitting}>
              {isSubmitting ? "Importing..." : `Import ${rows.length} product${rows.length === 1 ? "" : "s"}`}
            </button>
            <button className="btn" onClick={reset} disabled={isSubmitting}>Clear</button>
          </div>
        </>
      )}

      {results && (
        <div>
          <p style={{ fontSize: "13px", marginBottom: "8px" }}>
            <strong>{results.created_count}</strong> added, <strong>{results.updated_count}</strong> updated
            {results.failure_count > 0 && <>, <strong style={{ color: "var(--danger)" }}>{results.failure_count}</strong> failed</>}.
          </p>
          {failures.length > 0 && (
            <table className="data-table" style={{ width: "100%", fontSize: "12.5px", marginBottom: "10px" }}>
              <thead><tr><th>Row</th><th>Product</th><th>Problem</th></tr></thead>
              <tbody>
                {failures.slice(0, 50).map((f) => (
                  <tr key={f.row_number}><td>{f.row_number}</td><td>{f.name}</td><td style={{ color: "var(--danger)" }}>{f.error}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          {failures.length > 50 && <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>…and {failures.length - 50} more failures.</p>}
          <button className="btn" onClick={reset}>Import another file</button>
        </div>
      )}
    </div>
  );
}

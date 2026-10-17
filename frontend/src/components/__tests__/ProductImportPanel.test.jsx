import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const bulkImportProducts = vi.fn();
const showToast = vi.fn();

vi.mock("../../context/AuthContext", () => ({ useAuth: () => ({ token: "tok" }) }));
vi.mock("../../context/ToastContext", () => ({ useToast: () => ({ showToast }) }));
vi.mock("../../services/api", () => ({ bulkImportProducts: (...args) => bulkImportProducts(...args) }));

import ProductImportPanel, { mapColumns, findRowProblems } from "../ProductImportPanel";

function csvFile(text, name = "products.csv") {
  return new File([text], name, { type: "text/csv" });
}

describe("mapColumns", () => {
  it("maps common spreadsheet header names onto ours and reports ignored ones", () => {
    const { rows, ignoredColumns, mapped } = mapColumns([
      { "Product Name": " Rice ", "MRP": "649", "Qty": "10", "Supplier": "Acme" },
    ]);
    expect(rows).toEqual([{ name: "Rice", price: "649", stock_quantity: "10" }]);
    expect(ignoredColumns).toEqual(["Supplier"]);
    expect(mapped).toEqual(["name", "price", "stock_quantity"]);
  });
});

describe("findRowProblems", () => {
  it("flags empty names, missing prices and non-numeric prices, but accepts ₹ and commas", () => {
    const problems = findRowProblems([
      { name: "A", price: "₹1,299.50" },
      { name: "", price: "5" },
      { name: "B", price: "" },
      { name: "C", price: "abc" },
    ]);
    expect(problems.map((p) => p.row)).toEqual([2, 3, 4]);
  });
});

describe("ProductImportPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("previews the file, sends the rows, and shows the per-row report", async () => {
    const user = userEvent.setup();
    const onImportComplete = vi.fn();
    bulkImportProducts.mockResolvedValue({
      created_count: 1, updated_count: 0, failure_count: 1,
      results: [
        { row_number: 1, name: "Rice", success: true, action: "created" },
        { row_number: 2, name: "Dal", success: false, error: "price is required." },
      ],
    });
    render(<ProductImportPanel onImportComplete={onImportComplete} onClose={() => {}} />);

    await user.upload(screen.getByLabelText(/choose a csv file/i), csvFile("name,price\nRice,649\nDal,\n"));
    expect(await screen.findByText(/2 products found/i)).toBeInTheDocument();
    expect(screen.getByText(/1 row will be skipped/i)).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /import 2 products/i }));

    await waitFor(() => expect(bulkImportProducts).toHaveBeenCalledWith("tok", [{ name: "Rice", price: "649" }, { name: "Dal", price: "" }], true));
    expect(await screen.findByText("price is required.")).toBeInTheDocument();
    expect(onImportComplete).toHaveBeenCalled();
  });

  it("explains a file that's missing the required columns", async () => {
    const user = userEvent.setup();
    render(<ProductImportPanel onImportComplete={() => {}} onClose={() => {}} />);
    await user.upload(screen.getByLabelText(/choose a csv file/i), csvFile("sku,colour\n1,red\n"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/missing required column\(s\): name, price/i);
    expect(screen.queryByRole("button", { name: /^import/i })).not.toBeInTheDocument();
  });
});

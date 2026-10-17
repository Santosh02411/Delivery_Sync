import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import VerificationBanner from "../VerificationBanner";

function setup(overrides = {}) {
  const props = {
    onResend: vi.fn().mockResolvedValue({}),
    onVerify: vi.fn().mockResolvedValue({}),
    onVerified: vi.fn(),
    ...overrides,
  };
  render(<VerificationBanner {...props} />);
  return props;
}

describe("VerificationBanner (email OTP)", () => {
  it("auto-submits when the sixth digit is typed and reports success", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.type(screen.getByLabelText(/6-digit verification code/i), "123456");
    await waitFor(() => expect(props.onVerify).toHaveBeenCalledWith("123456"));
    await waitFor(() => expect(props.onVerified).toHaveBeenCalled());
    expect(screen.getByRole("status")).toHaveTextContent(/verified/i);
  });

  it("only accepts digits and keeps Verify disabled until all six are entered", async () => {
    const user = userEvent.setup();
    const props = setup();
    const input = screen.getByLabelText(/6-digit verification code/i);
    await user.type(input, "12ab3");
    expect(input).toHaveValue("123");
    expect(screen.getByRole("button", { name: /^verify$/i })).toBeDisabled();
    expect(props.onVerify).not.toHaveBeenCalled();
  });

  it("shows the server's error and clears the box when the code is wrong", async () => {
    const user = userEvent.setup();
    const props = setup({ onVerify: vi.fn().mockRejectedValue(new Error("Incorrect code. 4 attempts left.")) });
    const input = screen.getByLabelText(/6-digit verification code/i);
    await user.type(input, "000000");
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect code. 4 attempts left.");
    expect(input).toHaveValue("");
    expect(props.onVerified).not.toHaveBeenCalled();
  });

  it("resends a code and then enforces a cooldown", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole("button", { name: /resend code/i }));
    await waitFor(() => expect(props.onResend).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: /resend in \d+s/i })).toBeDisabled();
  });
});

/** Create an account, then sign straight in with the same credentials. */

import { useState } from "react";
import { Link } from "react-router";

import { register } from "../api/auth";
import { errorMessage } from "../api/client";
import { AuthCard } from "../components/AuthCard";
import { MIN_PASSWORD_CHARS } from "../constants";
import { useAuth } from "../hooks/useAuth";

export function RegisterPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    // Mirror the backend rule so the common mistake is caught without a round trip.
    if (password.length < MIN_PASSWORD_CHARS) {
      setError(`Password must be at least ${MIN_PASSWORD_CHARS} characters.`);
      return;
    }
    setPending(true);
    setError(null);
    const address = email.trim();
    try {
      await register({ email: address, password });
      await login(address, password);
    } catch (caught) {
      setError(errorMessage(caught));
      setPending(false);
    }
  }

  return (
    <AuthCard
      title="Create your account"
      subtitle="Free for the project demo. Doctor AI answers only from the uploaded references."
      submitLabel="Create account"
      pending={pending}
      error={error}
      email={email}
      password={password}
      passwordHint={`At least ${MIN_PASSWORD_CHARS} characters.`}
      onEmailChange={setEmail}
      onPasswordChange={setPassword}
      onSubmit={() => void submit()}
      autoCompletePassword="new-password"
      footer={
        <>
          Already registered?{" "}
          <Link to="/login" className="font-medium text-teal-700 hover:underline">
            Sign in
          </Link>
        </>
      }
    />
  );
}

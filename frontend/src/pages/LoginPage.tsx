/** Sign in. Navigation after success is handled by <RedirectIfAuthed> around this page. */

import { useState } from "react";
import { Link } from "react-router";

import { errorMessage } from "../api/client";
import { AuthCard } from "../components/AuthCard";
import { useAuth } from "../hooks/useAuth";

export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setPending(true);
    setError(null);
    try {
      await login(email.trim(), password);
    } catch (caught) {
      setError(errorMessage(caught));
      setPending(false);
    }
  }

  return (
    <AuthCard
      title="Sign in"
      subtitle="Welcome back. Sign in to talk to Doctor AI."
      submitLabel="Sign in"
      pending={pending}
      error={error}
      email={email}
      password={password}
      onEmailChange={setEmail}
      onPasswordChange={setPassword}
      onSubmit={() => void submit()}
      autoCompletePassword="current-password"
      footer={
        <>
          New here?{" "}
          <Link to="/register" className="font-medium text-teal-700 hover:underline">
            Create an account
          </Link>
        </>
      }
    />
  );
}

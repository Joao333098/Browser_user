import { Switch, Route, Router as WouterRouter, Link, useLocation } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import ChatPage from "@/pages/ChatPage";
import BrowserAgentPage from "@/pages/BrowserAgentPage";
import { MessageSquare, Globe } from "lucide-react";

const queryClient = new QueryClient();

function Nav() {
  const [location] = useLocation();
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-1 bg-black/60 backdrop-blur border border-white/10 rounded-2xl px-2 py-1.5 shadow-xl">
      <Link
        href="/"
        className={`flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-medium transition-all ${
          location === "/" ? "bg-white/15 text-white" : "text-white/40 hover:text-white/70"
        }`}
      >
        <MessageSquare className="w-3.5 h-3.5" />
        Chat
      </Link>
      <Link
        href="/browser-agent"
        className={`flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-medium transition-all ${
          location === "/browser-agent" ? "bg-white/15 text-white" : "text-white/40 hover:text-white/70"
        }`}
      >
        <Globe className="w-3.5 h-3.5" />
        Browser Agent
      </Link>
    </div>
  );
}

function Router() {
  return (
    <Switch>
      <Route path="/" component={ChatPage} />
      <Route path="/browser-agent" component={BrowserAgentPage} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Nav />
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;

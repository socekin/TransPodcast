import { routeRequest } from "./router";

export default {
  async fetch(request, env): Promise<Response> {
    try {
      return await routeRequest(request, env);
    } catch (error) {
      console.error(
        JSON.stringify({
          message: "publication request failed",
          error: error instanceof Error ? error.message : String(error),
        }),
      );
      return new Response("Not found", { status: 404 });
    }
  },
} satisfies ExportedHandler<Env>;

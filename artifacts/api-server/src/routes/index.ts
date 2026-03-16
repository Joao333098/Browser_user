import { Router, type IRouter } from "express";
import healthRouter from "./health";
import chatRouter from "./chat";
import browserRouter from "./browser";

const router: IRouter = Router();

router.use(healthRouter);
router.use(chatRouter);
router.use(browserRouter);

export default router;

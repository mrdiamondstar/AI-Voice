import { NextResponse } from 'next/server';
import { sipClient, roomService, agentDispatchClient } from '@/lib/server-utils';

export async function POST(request: Request) {
    try {
        const body = await request.json();
        const { phoneNumber, prompt, modelProvider, voice, sector } = body;

        if (!phoneNumber) {
            return NextResponse.json({ error: "Phone number is required" }, { status: 400 });
        }

        const trunkId = process.env.VOBIZ_SIP_TRUNK_ID;
        if (!trunkId) {
            console.error("VOBIZ_SIP_TRUNK_ID is missing in env");
            return NextResponse.json({ error: "SIP Trunk not configured" }, { status: 500 });
        }

        // Generate a unique room name for this call
        const roomName = `call-${phoneNumber.replace(/\+/g, '')}-${Math.floor(Math.random() * 10000)}`;
        const particpantIdentity = `sip_${phoneNumber}`;

        console.log(`Dispatching call to ${phoneNumber} in room ${roomName} via trunk ${trunkId}`);

        // agent.py registers with agent_name="outbound-caller", which puts it in
        // "explicit dispatch" mode - it will NOT auto-join rooms on its own.
        // We must call agentDispatchClient.createDispatch() below or the SIP call
        // connects to an empty room (call answers but there's no agent, so silence).

        const metadata = JSON.stringify({
            phone_number: phoneNumber,
            user_prompt: prompt || "",
            model_provider: modelProvider || "openai",
            voice_id: voice || "alloy",
            sector: sector || ""
        });

        // Create the room explicitly with metadata first - createSipParticipant
        // has no "roomMetadata" option, so the room must carry it up front.
        await roomService.createRoom({
            name: roomName,
            metadata,
            emptyTimeout: 60 * 5,
        });

        const info = await sipClient.createSipParticipant(
            trunkId,
            phoneNumber,
            roomName,
            {
                participantIdentity: particpantIdentity,
                participantName: "Customer",
            }
        );

        // Explicitly dispatch the "outbound-caller" agent into this room so it
        // actually joins and speaks once the call connects.
        await agentDispatchClient.createDispatch(roomName, "outbound-caller", {
            metadata,
        });

        return NextResponse.json({
            success: true,
            roomName,
            dispatchId: info.sipCallId
        });

    } catch (error: any) {
        console.error("Error dispatching call:", error);
        return NextResponse.json({ error: error.message || "Internal Server Error" }, { status: 500 });
    }
}

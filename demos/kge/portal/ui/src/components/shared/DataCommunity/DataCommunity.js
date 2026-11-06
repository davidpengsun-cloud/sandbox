import { Accordion, AccordionDetails, AccordionSummary, Grid, List, ListItem, ListItemText, Typography } from "@material-ui/core";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import ExpandMoreIcon from '@material-ui/icons/ExpandMore';
import './DataCommunity.css';

export default function DataCommunity(props) {
    const navigate = useNavigate();
    // let [communityList, setCommunityList] = useState(props.content);
    let communityList = props.content;
    const handleChange = (id) => () => {
        // setCommunityList(
        //     communityList.map((item) => {
        //         if (item.id !== id && item.expanded) {
        //             item.expanded = !item.expanded;
        //         }
        //         if (item.id === id) {
        //             item.expanded = !item.expanded;
        //         }
        //         return item;
        //     })
        // );
    };

    return (
        <>
            {communityList.length > 0 ? (
                <Grid container spacing={2} id="main">
                    {communityList.map((community, index) => (
                        <Grid key={index} item xs={12} className="contentLeft">
                            <Accordion expanded={community.expanded} onChange={handleChange(community.name)} className="accordian">
                                <AccordionSummary
                                    expandIcon={<ExpandMoreIcon />}
                                >
                                    <Grid item xs={12} className="content">
                                        <Typography variant="body2" color="textPrimary" className="title">{community.name}</Typography>
                                        <Typography variant="h5" color="textPrimary">{community.description.length >=50 ? `${community.description?.substr(0, 50)}...` : community.description}</Typography>
                                    </Grid>
                                </AccordionSummary>
                                <AccordionDetails className="accordianDetails">
                                    {community.domain.map((submenu, index) => (
                                        <List component="nav">
                                            <ListItem className="nested">
                                                <Grid item xs={12} className="subContent">
                                                    <ListItemText primary={submenu.name} onClick={() => navigate("/search")}
                                                        secondary={
                                                            <Typography variant="h5" color="textPrimary">{submenu.description.length >=50 ? `${submenu.description?.substr(0, 50)}...` : submenu.description}</Typography>

                                                        }
                                                    />
                                                </Grid>
                                            </ListItem>
                                        </List>
                                    ))}
                                </AccordionDetails>
                            </Accordion>
                        </Grid>
                    ))}
                </Grid>
            ) : (
                <></>
            )}
        </>
    )
}